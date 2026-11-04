"""Save each lead's verified phone line type.

Revision ID: 0040_lead_phone_line_type
Revises: 0039_inbound_sms_events

Additive only: three nullable columns on ``leads``. Leads whose current number
was already verified by Telnyx (stored in ``details._phone_numbers``) are
backfilled so confirmed non-mobile numbers leave the pool right away.
"""

import json
from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision = "0040_lead_phone_line_type"
down_revision = "0039_inbound_sms_events"
branch_labels = None
depends_on = None

COLUMNS = ("phone_line_type", "phone_lookup_number", "phone_lookup_at")


def _digits(value):
    return "".join(ch for ch in str(value or "") if ch.isdigit())[-10:]


def _checked_at(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def upgrade():
    bind = op.get_bind()
    existing = {column["name"] for column in sa.inspect(bind).get_columns("leads")}
    if "phone_line_type" not in existing:
        op.add_column("leads", sa.Column("phone_line_type", sa.String(length=30), nullable=True))
    if "phone_lookup_number" not in existing:
        op.add_column("leads", sa.Column("phone_lookup_number", sa.String(length=50), nullable=True))
    if "phone_lookup_at" not in existing:
        op.add_column("leads", sa.Column("phone_lookup_at", sa.DateTime(), nullable=True))

    leads = sa.table(
        "leads",
        sa.column("id", sa.Integer),
        sa.column("phone", sa.String),
        sa.column("details", sa.Text),
        sa.column("phone_line_type", sa.String),
        sa.column("phone_lookup_number", sa.String),
        sa.column("phone_lookup_at", sa.DateTime),
    )
    rows = bind.execute(
        sa.select(leads.c.id, leads.c.phone, leads.c.details).where(
            leads.c.phone.isnot(None),
            leads.c.phone_line_type.is_(None),
            leads.c.details.like("%telnyx_lookup%"),
        )
    ).fetchall()
    for lead_id, phone, details in rows:
        try:
            numbers = (json.loads(details or "{}") or {}).get("_phone_numbers") or []
        except ValueError:
            continue
        for item in numbers if isinstance(numbers, list) else []:
            if not isinstance(item, dict):
                continue
            number = item.get("phone") or item.get("number")
            line_type = str(item.get("type") or "").strip().lower()
            if (item.get("lookup_status") == "verified" and line_type
                    and _digits(number) and _digits(number) == _digits(phone)):
                bind.execute(
                    leads.update().where(leads.c.id == lead_id).values(
                        phone_line_type=line_type,
                        phone_lookup_number=phone,
                        phone_lookup_at=_checked_at(item.get("lookup_checked_at")),
                    )
                )
                break


def downgrade():
    existing = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("leads")}
    for column in COLUMNS:
        if column in existing:
            op.drop_column("leads", column)
