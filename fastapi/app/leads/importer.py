"""CSV and XLSX ingestion for the lead pool.

Brokers buy lists from many vendors, so the column names are never the same
twice. Rather than demand one exact header, we recognise a set of aliases per
field and additionally treat any column named after a signal as a flag column
("fsbo,high_equity" with 1/yes/x values).
"""

from __future__ import annotations

import csv
import io
import json
import re
from typing import Iterable

from .catalog import SIGNALS, normalize_signal, parse_signals

# Field -> header aliases, all compared lower-cased with separators stripped.
_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    'owner_name': ('owner', 'ownername', 'name', 'fullname', 'ownerfullname', 'contactname', 'seller'),
    'first_name': ('firstname', 'ownerfirstname', 'first'),
    'last_name': ('lastname', 'ownerlastname', 'last', 'surname'),
    'phone': ('phone', 'phonenumber', 'mobile', 'cell', 'cellphone', 'contact', 'contactnumber', 'telephone'),
    'property_address': ('propertyaddress', 'address', 'property', 'street', 'streetaddress', 'siteaddress'),
    'area': ('area', 'city', 'neighborhood', 'neighbourhood', 'market', 'town', 'submarket'),
    'signals': ('signals', 'signal', 'tags', 'tag', 'leadtype', 'lead_type', 'category',
                'motivation', 'leadsource', 'source', 'listingstatus', 'status'),
    # Why this owner is being contacted, in the vendor's own words.
    'outreach_reason': ('outreachreason', 'reason', 'outreachnotes', 'note', 'notes'),
    # A JSON blob of property attributes (beds, baths, price, …).
    'details': ('leaddetails', 'details', 'propertydetails', 'propertyinfo', 'attributes'),
}

# Attributes inside a details blob that imply a signal. Only facts the record
# states outright — nothing is inferred beyond what the data says.
_DETAIL_SIGNALS: tuple[tuple[str, str, str], ...] = (
    ('listed_for_sale_by_owner', 'yes', 'fsbo'),
    ('owner_occ', 'no', 'absentee_owner'),
    ('vacant', 'yes', 'vacant'),
    ('pre_foreclosure', 'yes', 'pre_foreclosure'),
)

_TRUTHY = {'1', 'y', 'yes', 'true', 't', 'x', 'active'}


def _key(header: str) -> str:
    return re.sub(r'[^a-z0-9]+', '', str(header or '').lower())


# Fields a broker may map by hand when the guess is wrong.
MAPPABLE_FIELDS = ('owner_name', 'first_name', 'last_name', 'phone',
                   'property_address', 'area', 'signals', 'outreach_reason', 'details')


def _map_headers(fieldnames: list[str],
                 overrides: dict[str, str] | None = None) -> tuple[dict[str, str], dict[str, str]]:
    """Return (field -> column, signal key -> column) for one header row.

    An override wins over the guess, and an override of '' means "no column" —
    a broker must be able to say a detected column is the wrong one.
    """
    fields: dict[str, str] = {}
    flags: dict[str, str] = {}
    chosen: set[str] = set()

    for field, column in (overrides or {}).items():
        if field not in MAPPABLE_FIELDS:
            continue
        if column == '':
            # Explicitly unmapped: record nothing, and block the auto-guess.
            chosen.add(field)
            continue
        if column in (fieldnames or []):
            fields[field] = column
            chosen.add(field)

    for column in fieldnames or []:
        if column in fields.values():
            continue
        key = _key(column)
        if not key:
            continue
        for field, aliases in _FIELD_ALIASES.items():
            if field in fields or field in chosen:
                continue
            if key in {_key(alias) for alias in aliases}:
                fields[field] = column
                break
        else:
            signal = normalize_signal(column)
            if signal and signal not in flags:
                flags[signal] = column
    return fields, flags


def normalize_phone(value: str) -> str | None:
    """Best-effort E.164. North American numbers are assumed when unprefixed."""
    raw = str(value or '').strip()
    if not raw:
        return None
    plus = raw.startswith('+')
    digits = re.sub(r'\D', '', raw)
    if not digits:
        return None
    if plus:
        return f'+{digits}' if 7 <= len(digits) <= 15 else None
    if len(digits) == 10:
        return f'+1{digits}'
    if len(digits) == 11 and digits.startswith('1'):
        return f'+{digits}'
    return None


def parse_details(value: object) -> dict:
    """A property-details cell as a flat dict of strings.

    Vendors ship these as a JSON object in one column. Anything that is not an
    object is ignored rather than guessed at.
    """
    if isinstance(value, dict):
        parsed = value
    else:
        text = str(value or '').strip()
        if not text.startswith('{'):
            return {}
        try:
            parsed = json.loads(text)
        except ValueError:
            return {}
    if not isinstance(parsed, dict):
        return {}
    return {str(key): str(item) for key, item in parsed.items()
            if item is not None and str(item).strip() != ''}


def signals_from_details(details: dict) -> list[str]:
    """Signals the details blob states outright."""
    found = []
    for key, wanted, signal in _DETAIL_SIGNALS:
        if str(details.get(key, '')).strip().lower() == wanted and signal not in found:
            found.append(signal)
    return found


def score_lead(signals: list[str], has_phone: bool, has_address: bool) -> int:
    """A transparent 0-100 sum of signal weights plus contact completeness.

    Deliberately simple and explainable: this orders a review queue, it does not
    predict anything.
    """
    total = sum(SIGNALS.get(key, ('', 0))[1] for key in signals)
    total += 20 if has_phone else 0
    total += 10 if has_address else 0
    return max(0, min(100, total))


def parse_csv(content: bytes, overrides: dict[str, str] | None = None):
    """Parse an uploaded CSV into normalized lead dicts, warnings and column meta."""
    try:
        text = content.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = content.decode('latin-1')

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        return [], ['The file has no header row.'], _empty_meta()
    return _read_rows(list(reader.fieldnames), reader, overrides)


def _text(cell: object) -> str:
    """One spreadsheet cell as text.

    Whole numbers come back as floats often enough that 3125550142.0 would
    otherwise be read as an eleven-digit phone number.
    """
    if cell is None:
        return ''
    if isinstance(cell, float) and cell.is_integer():
        return str(int(cell))
    return str(cell).strip()


def parse_workbook(content: bytes, overrides: dict[str, str] | None = None):
    """Parse the first sheet of an .xlsx workbook using the same column rules.

    Only the first sheet is read: a lead list is one table, and silently
    merging extra sheets would import data the broker did not see.
    """
    try:
        from openpyxl import load_workbook
    except ImportError:  # pragma: no cover - openpyxl is a pinned dependency
        return [], ['Excel files need the openpyxl package on the server.'], _empty_meta()

    try:
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as error:  # noqa: BLE001 - openpyxl raises many shapes
        return [], [f'The workbook could not be opened: {error}'], _empty_meta()

    try:
        sheet = workbook[workbook.sheetnames[0]]
        rows = sheet.iter_rows(values_only=True)
        header: list[str] = []
        for row in rows:
            values = [_text(cell) for cell in row]
            if any(values):
                header = values
                break
        if not header:
            return [], ['The workbook has no header row.'], _empty_meta()

        records = []
        for row in rows:
            values = [_text(cell) for cell in row]
            if not any(values):
                continue
            # Pad or trim so ragged rows still line up with the header.
            values += [''] * (len(header) - len(values))
            records.append(dict(zip(header, values[:len(header)])))
    finally:
        workbook.close()

    return _read_rows(header, records, overrides)


def parse_upload(content: bytes, filename: str, overrides: dict[str, str] | None = None):
    """Dispatch on file extension. Anything else is rejected by the route."""
    if filename.lower().endswith('.xlsx'):
        return parse_workbook(content, overrides)
    return parse_csv(content, overrides)


def _empty_meta() -> dict:
    return {'columns': [], 'mapping': {}, 'signal_columns': {}}


def _read_rows(fieldnames: list[str], rows: Iterable[dict],
               overrides: dict[str, str] | None = None):
    """Map header-aligned dict rows onto normalized leads. Shared by CSV and XLSX."""
    fields, flags = _map_headers(list(fieldnames), overrides)
    meta = {
        'columns': list(fieldnames),
        'mapping': {field: fields.get(field) for field in MAPPABLE_FIELDS},
        'signal_columns': dict(flags),
    }
    if 'phone' not in fields and 'property_address' not in fields:
        return [], ['No phone or property address column was recognised. '
                    'Choose which columns hold the phone number and the property address.'], meta

    leads: list[dict] = []
    warnings: list[str] = []
    for number, row in enumerate(rows, start=2):
        def cell(field: str) -> str:
            column = fields.get(field)
            return str(row.get(column) or '').strip() if column else ''

        name = cell('owner_name')
        if not name:
            name = ' '.join(part for part in (cell('first_name'), cell('last_name')) if part).strip()

        phone = normalize_phone(cell('phone'))
        address = cell('property_address')
        if not phone and not address:
            if any(str(value or '').strip() for value in row.values()):
                warnings.append(f'Row {number}: no usable phone or property address — skipped.')
            continue
        if cell('phone') and not phone:
            warnings.append(f'Row {number}: "{cell("phone")}" is not a usable phone number.')

        signals = parse_signals(cell('signals'))
        for signal, column in flags.items():
            if str(row.get(column) or '').strip().lower() in _TRUTHY and signal not in signals:
                signals.append(signal)

        details = parse_details(row.get(fields['details'])) if 'details' in fields else {}
        for signal in signals_from_details(details):
            if signal not in signals:
                signals.append(signal)

        leads.append({
            'owner_name': name[:255] or None,
            'phone': phone,
            'property_address': address[:500] or None,
            'area': cell('area')[:120] or None,
            'signals': signals,
            'outreach_reason': cell('outreach_reason')[:300] or None,
            'details': details,
            'score': score_lead(signals, bool(phone), bool(address)),
        })

    if not leads and not warnings:
        warnings.append('The file contained no data rows.')
    return leads, warnings, meta
