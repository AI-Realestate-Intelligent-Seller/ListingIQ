"""Deterministic reader for documents that follow the ListingIQ Persona template.

It maps the template's sections, labelled fields, playbook steps and
conversation examples straight into the Persona extraction contract, without
any AI provider. Nothing is inferred: text is copied as written, values that
do not fit a field are kept as unmapped details, and anything missing or
ambiguous becomes a clarification question for the owner.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from .schemas import PersonaExtractionContract
from .template_document import ADDITIONAL_QUESTION, ADDITIONAL_TITLE, COMMUNICATION_QUESTIONS, INSTRUCTION_LINES

SECTIONS = {
    "identity": "who i am",
    "background": "professional background",
    "communication": "communication dna",
    "playbook": "conversation playbook",
    "examples": "conversation examples",
}
SECTION_TITLES = {
    "identity": "Who I Am", "background": "Professional Background", "communication": "Communication DNA",
    "playbook": "Conversation Playbook", "examples": "Conversation Examples", "preamble": "Document notes",
    "selling": "Selling Approach and Services", "knowledge": "Knowledge Base",
}
# Factual sections. Their content is kept as source details for owner review,
# grouped under its field label; details the owner keeps become knowledge.
KNOWLEDGE_SECTIONS = {"selling": "selling approach", "knowledge": "knowledge base"}
# Template field label prefixes (normalized) per section.
FIELDS = {
    "identity": (
        ("name", "full name"), ("role", "professional role"), ("license", "license number"),
        ("contact", "public professional email"), ("intro", "professional introduction"),
    ),
    "background": (
        ("bio", "biography"), ("expertise", "industry expertise"), ("philosophy", "professional philosophy"),
    ),
    "communication": (
        ("personality", "personality tone"), ("length", "sms and email length"), ("vocabulary", "vocabulary and"),
        ("languages", "languages and language"), ("behaviors", "how i communicate"),
    ),
}
PLAYBOOK = (
    ("introduc", "introduction"), ("build trust", "trust_building"), ("trust", "trust_building"),
    ("qualif", "qualifying_questions"), ("valuation", "valuation_conversations"),
    ("buyer", "buyer_vs_seller"), ("investor", "investors_and_owners"),
    ("pricing", "pricing_disagreements"), ("objection", "objections"), ("follow", "follow_up"),
    ("appointment", "appointments"), ("escalat", "sensitive_escalation"), ("hand", "human_handoff"),
)
LANGUAGE_CODES = {
    "english": "en", "korean": "ko", "spanish": "es", "french": "fr", "chinese": "zh", "mandarin": "zh",
    "cantonese": "yue", "japanese": "ja", "vietnamese": "vi", "tagalog": "tl", "hindi": "hi", "urdu": "ur",
    "arabic": "ar", "portuguese": "pt", "russian": "ru", "polish": "pl", "german": "de", "italian": "it",
    "punjabi": "pa", "farsi": "fa", "persian": "fa",
}
ROLE_WORDS = ("broker", "agent", "realtor", "salesperson", "associate", "advisor", "adviser")
_ABBREVIATIONS = ("inc", "llc", "ltd", "co", "corp", "st", "ave", "dr", "mr", "mrs", "ms", "jr", "sr", "e.g", "i.e", "vs", "no")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")
_BULLET = re.compile(r"^(\s*)(?:[-*•–○◦▪■●]|\d+[.)])\s+")
# Second-level bullet glyphs that PDF text keeps without indentation.
_SUB_BULLETS = "○◦▪■"
# Statements that read as achievements, rankings or statistics; kept as claims
# for the owner to review, never treated as verified.
_CLAIM_WORDS = re.compile(
    r"\d|%|#1|\b(?:top|largest|biggest|bigger|best|leading|number one|award\w*|ranked|ranking|record|followers|market share)\b",
    re.IGNORECASE,
)
_NUMBERED = re.compile(r"^\s*(\d+)[.)]\s+")


def _norm(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).lower()
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s&/]", " ", value)).strip()


_INSTRUCTIONS = frozenset(_norm(line) for line in INSTRUCTION_LINES)


def _plain(value: str) -> str:
    """Remove Markdown emphasis markers without changing the words."""
    value = re.sub(r"\*\*(.+?)\*\*", r"\1", value)
    value = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"\1", value)
    value = re.sub(r"(?<!\w)_(?!\s)(.+?)(?<!\s)_(?!\w)", r"\1", value)
    return unicodedata.normalize("NFC", value).strip()


def _unquote(value: str) -> str:
    value = value.strip()
    pairs = (('"', '"'), ("“", "”"), ("'", "'"), ("‘", "’"))
    for start, end in pairs:
        marks = value.count(start) if start == end else value.count(start) + value.count(end)
        if len(value) >= 2 and value.startswith(start) and value.endswith(end) and marks == 2:
            return value[1:-1].strip()
    return value


def _sentences(text: str) -> list[str]:
    parts, current = [], ""
    for token in re.split(r"(?<=[.!?])\s+", text.strip()):
        current = f"{current} {token}".strip() if current else token
        last = current.rsplit(" ", 1)[-1].rstrip(".").lower()
        if last in _ABBREVIATIONS or re.fullmatch(r"[a-z]", last):
            continue
        parts.append(current)
        current = ""
    if current:
        parts.append(current)
    return parts


def _split_items(text: str) -> list[str]:
    """Split a written list on commas/semicolons outside parentheses."""
    items, depth, current = [], 0, ""
    for character in text:
        depth += character in "([{"
        depth -= character in ")]}"
        if character in ",;" and depth <= 0:
            items.append(current)
            current = ""
        else:
            current += character
    items.append(current)
    cleaned = []
    for item in items:
        item = re.sub(r"^\s*(?:and|plus|or)\s+", "", item.strip()).strip().rstrip(".").strip()
        if item:
            cleaned.append(item)
    return cleaned


def _is_not_available(value: str) -> bool:
    return bool(re.match(r"^\s*(?:n/?a\b|none\b|not (?:provided|available|applicable)|unknown\b|-+$)", value, re.IGNORECASE))


@dataclass
class Line:
    text: str
    depth: int
    label: str | None = None
    value: str = ""
    quote: bool = False


@dataclass
class Group:
    label: str | None
    values: list[str] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(value for value in self.values if value).strip()

    @property
    def excerpt(self) -> str:
        """The group's source lines, one per line, for evidence."""
        return "\n".join(value for value in self.values if value).strip()


def _parse_line(raw: str) -> Line | None:
    if not raw.strip():
        return None
    quote = raw.lstrip().startswith(">")
    body = re.sub(r"^\s*>\s?", "", raw) if quote else raw
    match = _BULLET.match(body)
    indent = len(body) - len(body.lstrip(" \t"))
    depth = indent // 2 + (1 if match else 0) + (1 if match and body.lstrip()[:1] in _SUB_BULLETS else 0)
    text = body[match.end():] if match else body.strip()
    line = Line(text=_plain(text), depth=depth, quote=quote)
    if quote:
        return line
    labelled = re.match(r"^\*\*([^*]{1,80}?):\*\*\s*(.*)$", text) or re.match(r"^\*\*([^*]{1,80}?)\*\*:\s*(.*)$", text)
    if not labelled:
        labelled = re.match(r"^([A-Z][^:.!?\"“]{0,100}):\s*(.*)$", _plain(text))
    if labelled:
        line.label = _plain(labelled.group(1)).strip()
        line.value = _plain(labelled.group(2)).strip()
    return line


class _Builder:
    def __init__(self) -> None:
        self.draft: dict = {}
        self.style: dict = {}
        self.profile: dict = {"identity": {}, "background": {}, "communication": {}, "playbook": {}}
        self.examples: list[dict] = []
        self.evidence: list[dict] = []
        self.claims: list[dict] = []
        self.unmapped: list[dict] = []
        self.questions: list[str] = []
        self.mapped_fields = 0

    # Values are stored only when they fit; otherwise the full text is kept.
    def put(self, path: str, value, *, limit: int, excerpt: str, section: str, confidence: str = "high") -> bool:
        if value in (None, "", []):
            return False
        if isinstance(value, str) and len(value) > limit:
            self.keep(section, f"{path.rsplit('.', 1)[-1].replace('_', ' ').capitalize()}: {value}")
            self.questions.append(f"'{path.rsplit('.', 1)[-1].replace('_', ' ')}' is longer than the field allows; shorten it in review")
            return False
        target, key = self._target(path)
        target[key] = value
        self.mapped_fields += 1
        self.evidence.append({
            "field_path": path, "source_excerpt": (excerpt or str(value))[:500], "confidence": confidence,
            "needs_owner_confirmation": confidence != "high",
        })
        return True

    def put_list(self, path: str, items: list[str], *, item_limit: int, max_items: int, excerpt: str, section: str,
                 confidence: str = "medium") -> None:
        fitted = [item for item in items if len(item) <= item_limit][:max_items]
        for item in items:
            if item not in fitted:
                self.keep(section, item)
        target, key = self._target(path)
        existing = target.get(key, [])
        merged = existing + [item for item in fitted if item not in existing]
        if merged:
            self.put(path, merged, limit=10**9, excerpt=excerpt, section=section, confidence=confidence)

    def _target(self, path: str):
        root, *rest = path.split(".")
        target = {"draft": self.draft, "style_config": self.style, "profile_config": self.profile}[root]
        for part in rest[:-1]:
            target = target.setdefault(part, {})
        return target, rest[-1]

    def keep(self, section: str, detail: str) -> None:
        detail = detail.strip()
        if not detail or _norm(detail) in _INSTRUCTIONS:
            # ListingIQ's own template guidance is not the owner's content.
            return
        if section.startswith("other:"):
            title = re.sub(r"#\d+$", "", section[len("other:"):])[:120] or "Other"
        else:
            title = SECTION_TITLES.get(section, section)[:120]
        for start in range(0, len(detail), 1000):
            entry = {"section": title, "detail": detail[start:start + 1000]}
            if entry not in self.unmapped:
                self.unmapped.append(entry)

    def remainder(self, section: str, raw: str, consumed: list[str]) -> None:
        rest = raw
        for part in consumed:
            if part:
                rest = rest.replace(part, " ")
        if len(re.sub(r"[\W_]+", "", rest, flags=re.UNICODE)) > 3:
            self.keep(section, raw)

    def claim(self, statement: str, path: str) -> None:
        status = "self_reported" if re.search(r"self[-\s]?reported|self[-\s]?declared", statement, re.IGNORECASE) else "unverified"
        entry = {"statement": statement[:500], "status": status, "field_path": path, "source_excerpt": statement[:500]}
        if entry not in self.claims:
            self.claims.append(entry)


def _groups(lines: list[Line]) -> list[Group]:
    groups: list[Group] = []
    label_depth = None
    for line in lines:
        if line.label is not None:
            groups.append(Group(line.label, [line.value] if line.value else []))
            label_depth = line.depth
            continue
        current = groups[-1] if groups else None
        joins = current is not None and current.label is not None and (not current.values or line.depth > (label_depth or 0))
        if joins:
            current.values.append(line.text)
        else:
            groups.append(Group(None, [line.text]))
            label_depth = None
    return groups


def _section_of(line: str) -> str | None:
    stripped = line.strip()
    heading = stripped.startswith("#")
    title = _norm(re.sub(r"^#+\s*", "", stripped))
    if not title or (not heading and len(title) > 40):
        return None
    for key, name in (*SECTIONS.items(), *KNOWLEDGE_SECTIONS.items()):
        if title == name or (heading and title.startswith(name)):
            return key
    return "other" if heading and len(stripped.lstrip("#").strip()) else None


def _split_sections(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {"preamble": []}
    current = "preamble"
    other_index = 0
    for raw in text.splitlines():
        found = _section_of(raw)
        if found == "other" and current == "examples" and (
            re.match(r"^\s*#+\s*(?:Example\s+)?\d+(?:[.)]|\s*[—–]|\s+-)\s", raw, re.IGNORECASE) or _ADDITIONAL_HEADER.match(raw)
        ):
            # A numbered example, a template question or an additional example written as a heading.
            found = None
        if found == "other":
            other_index += 1
            current = f"other:{_plain(raw.strip().lstrip('#').strip())}#{other_index}"
            sections[current] = []
            continue
        if found:
            current = found
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(raw)
    return sections


def _fields(section: str, raw_lines: list[str], builder: _Builder) -> dict[str, list[Line]]:
    found: dict[str, list[Line]] = {}
    current = None
    for raw in raw_lines:
        line = _parse_line(raw)
        if line is None:
            continue
        key = None
        if line.label is not None:
            label = _norm(line.label)
            key = next((name for name, prefix in FIELDS[section] if label.startswith(prefix)), None)
        if key:
            current = key
            found[key] = [Line(line.value, line.depth, None, "")] if line.value else []
            continue
        if current is None:
            builder.keep(section, line.text)
            continue
        found[current].append(line)
    return found


def _first_language_code(text: str) -> str | None:
    for word, code in LANGUAGE_CODES.items():
        if re.search(rf"\b{word}\b", text, re.IGNORECASE):
            return code
    return None


def _identity(lines: dict[str, list[Line]], b: _Builder) -> None:
    section = "identity"
    if "name" in lines:
        raw = " ".join(line.text for line in lines["name"])
        first = _sentences(raw)[0] if raw else ""
        consumed = []
        if first and len(first.rstrip(".")) <= 120 and ":" not in first:
            full = first.rstrip(".")
            b.put("draft.full_name", full, limit=255, excerpt=raw, section=section, confidence="medium")
            consumed.append(first)
        display = re.search(r"display name:\s*[\"“']([^\"”']+)[\"”']", raw, re.IGNORECASE)
        if display:
            b.put("draft.display_name", display.group(1).strip(), limit=120, excerpt=raw, section=section)
            consumed.append(display.group(0))
        b.remainder(section, f"Name: {raw}", ["Name:", *consumed])
    if "role" in lines:
        raw = " ".join(line.text for line in lines["role"])
        sentences = _sentences(raw)
        consumed = []
        if sentences:
            b.put("draft.professional_title", sentences[0].rstrip("."), limit=160, excerpt=raw, section=section, confidence="medium")
            consumed.append(sentences[0])
        role = next((s for s in sentences[1:] if any(word in s.lower() for word in ROLE_WORDS)), None)
        if role:
            b.put("draft.professional_role", role.rstrip("."), limit=120, excerpt=raw, section=section, confidence="medium")
            consumed.append(role)
        else:
            b.questions.append("Confirm your professional role (for example broker or agent)")
        b.remainder(section, f"Role: {raw}", ["Role:", *consumed])
    if "license" in lines:
        raw = " ".join(line.text for line in lines["license"])
        parts = re.split(r"\b(Jurisdiction|Brokerage|License number|License status|Status|Number)\s*:\s*", raw, flags=re.IGNORECASE)
        consumed = [parts[0]] if not parts[0].strip() else []
        if parts[0].strip():
            b.keep(section, f"Licensing: {parts[0].strip()}")
        for label, value in zip(parts[1::2], parts[2::2]):
            label = label.lower()
            value = value.strip()
            consumed.append(value)
            if label == "jurisdiction":
                b.put("profile_config.identity.licensing_jurisdiction", value.rstrip(".;"), limit=120, excerpt=raw, section=section)
            elif label == "brokerage" and value:
                b.keep(section, f"Brokerage stated in the document: {value}")
                b.questions.append("Your Persona uses the brokerage on your ListingIQ account; confirm it matches the brokerage stated in your document")
            elif label in ("status", "license status"):
                if _is_not_available(value):
                    b.questions.append(f"License status: {value}")
                else:
                    b.put("profile_config.identity.licensing_status", value.rstrip("."), limit=80, excerpt=raw, section=section)
            else:
                sentences = _sentences(value)
                stated = sentences[0] if sentences else value
                if _is_not_available(stated):
                    b.questions.extend(sentence for sentence in sentences[1:] if sentence)
                    if len(sentences) <= 1:
                        b.questions.append("Add your license number and confirm it is active")
                else:
                    b.put("profile_config.identity.license_number", stated.rstrip("."), limit=120, excerpt=raw, section=section)
                    for sentence in sentences[1:]:
                        b.keep(section, f"License note: {sentence}")
    if "contact" in lines:
        email_done = phone_done = False
        for line in lines["contact"]:
            text = line.text if line.label is None else f"{line.label}: {line.value}"
            emails, phones = _EMAIL.findall(text), _PHONE.findall(text)
            consumed = []
            if emails and not email_done:
                email_done = b.put("profile_config.identity.public_email", emails[0], limit=254, excerpt=text, section=section)
                consumed.append(emails[0])
            if phones and not phone_done:
                phone_done = b.put("profile_config.identity.public_phone", phones[0].strip(), limit=40, excerpt=text, section=section)
                consumed.append(phones[0])
            if line is lines["contact"][0] and line.label is None and not consumed:
                b.keep(section, text)
                continue
            leftover = text
            for part in consumed:
                leftover = leftover.replace(part, " ")
            leftover = re.sub(r"^[^:]{0,20}:\s*", "", leftover) if consumed else leftover
            if len(re.sub(r"[\W_]+", "", leftover)) > 1:
                b.keep(section, f"Contact: {text}")
    if "intro" in lines:
        raw = " ".join(line.text for line in lines["intro"])
        quoted = re.match(r"^\s*[\"“]([^\"”]+)[\"”]\s*", raw)
        rest = raw
        if quoted:
            b.put("profile_config.identity.professional_intro", quoted.group(1).strip(), limit=2000, excerpt=raw, section=section)
            rest = raw[quoted.end():].strip()
        brand = re.search(r"brand line:\s*[\"“]([^\"”]+)[\"”]", raw, re.IGNORECASE)
        if brand:
            b.put_list("profile_config.communication.brand_statements", [brand.group(1).strip()], item_limit=160, max_items=50,
                       excerpt=raw, section=section, confidence="high")
        if rest:
            b.put("profile_config.identity.brand_identity", rest, limit=1000, excerpt=raw, section=section)


def _background(lines: dict[str, list[Line]], b: _Builder) -> None:
    section = "background"
    if "bio" in lines:
        biography = []
        for group in _groups(lines["bio"]):
            label = _norm(group.label or "")
            if not group.label:
                biography.append(group.text)
            elif label.startswith("specialt"):
                b.put_list("draft.specialties", _split_items(group.text), item_limit=120, max_items=50, excerpt=group.excerpt, section=section)
            elif label.startswith("service area"):
                b.put_list("draft.service_areas", _split_items(group.text), item_limit=120, max_items=50, excerpt=group.excerpt, section=section)
            elif label.startswith("target audience"):
                b.put_list("profile_config.background.target_audiences", _split_items(group.text), item_limit=240, max_items=50,
                           excerpt=group.excerpt, section=section)
            elif label.startswith("biograph"):
                biography.append(group.text)
            elif label.startswith("residential") or label.startswith(("commercial", "business")):
                # Residential and business/commercial experience stay apart: one is
                # never evidence for the other. Each line is a claim for owner review.
                residential = label.startswith("residential")
                path = "profile_config.background." + ("transaction_experience" if residential else "business_background")
                scope = "Residential experience" if residential else "Commercial or business-sale experience"
                if group.text and not _is_not_available(group.text):
                    b.put(path, group.excerpt, limit=2000 if residential else 3000, excerpt=group.excerpt,
                          section=section, confidence="medium")
                    for value in group.values:
                        if value:
                            b.claim(f"{scope}: {value}", path)
            else:
                b.keep(section, f"{group.label}: {group.text}")
        if biography:
            b.put("draft.biography", "\n".join(biography), limit=5000, excerpt="\n".join(biography), section=section)
    if "expertise" in lines:
        for group in _groups(lines["expertise"]):
            label = _norm(group.label or "")
            if not group.label or label.startswith("achievement"):
                for value in group.values:
                    if len(value) <= 240:
                        b.put_list("profile_config.background.achievements", [value], item_limit=240, max_items=50,
                                   excerpt=value, section=section, confidence="high")
                    else:
                        b.keep(section, value)
                    b.claim(value, "profile_config.background.achievements")
            elif label.startswith("differentiator"):
                b.put_list("profile_config.background.differentiators", _split_items(group.text), item_limit=240, max_items=50,
                           excerpt=group.excerpt, section=section)
            elif label.startswith("industry expertise") or label.startswith("expertise"):
                b.put_list("profile_config.background.industry_expertise", _split_items(group.text), item_limit=240, max_items=50,
                           excerpt=group.excerpt, section=section)
            else:
                b.keep(section, f"{group.label}: {group.text}")
        self_reported = [claim for claim in b.claims if claim["status"] == "self_reported"]
        if self_reported:
            # Evidence is always quoted from the document itself.
            b.put("profile_config.background.transaction_claim_status", "self_declared", limit=40,
                  excerpt=self_reported[0]["source_excerpt"], section=section)
    if "philosophy" in lines:
        text = "\n".join(line.text if line.label is None else f"{line.label}: {line.value}" for line in lines["philosophy"])
        b.put("profile_config.background.professional_philosophy", text, limit=3000, excerpt=text, section=section)


def _vocabulary_groups(lines: list[Line]) -> list[Group]:
    """Like _groups, but every line nested under an "Avoid:" label is an avoided item.

    An avoided item may carry its own label ("Hype: \\"act now\\", ..."); that
    label must not end the Avoid list, or the remaining items get misfiled.
    """
    groups: list[Group] = []
    pending: list[Line] = []
    avoid: Group | None = None
    avoid_depth = 0
    for line in lines:
        if avoid is not None and line.depth > avoid_depth:
            avoid.values.append(line.text if line.label is None else f"{line.label}: {line.value}".strip())
            continue
        avoid = None
        if line.label is not None and _norm(line.label).startswith("avoid"):
            groups.extend(_groups(pending))
            pending = []
            avoid = Group(line.label, [line.value] if line.value else [])
            avoid_depth = line.depth
            groups.append(avoid)
            continue
        pending.append(line)
    groups.extend(_groups(pending))
    return groups


def _communication(lines: dict[str, list[Line]], b: _Builder) -> None:
    section = "communication"
    if "personality" in lines:
        raw = " ".join(line.text for line in lines["personality"])
        b.put("profile_config.communication.professional_casual_behavior", raw, limit=2000, excerpt=raw, section=section)
        summary = ""
        for sentence in _sentences(raw):
            if len(f"{summary} {sentence}".strip()) > 300:
                break
            summary = f"{summary} {sentence}".strip()
        if summary:
            b.put("style_config.personality", summary, limit=300, excerpt=raw, section=section)
    if "length" in lines:
        text = " ".join(line.text if line.label is None else f"{line.label}: {line.value}" for line in lines["length"])
        b.put("profile_config.communication.paragraph_style", text, limit=500, excerpt=text, section=section)
        if re.search(r"\b(one|1) question per message\b", text, re.IGNORECASE):
            b.put("profile_config.communication.questions_per_message", 1, limit=10, excerpt=text, section=section)
    if "vocabulary" in lines:
        for group in _vocabulary_groups(lines["vocabulary"]):
            label = _norm(group.label or "")
            if label in ("use", "say", "phrases to use") or label.startswith("signature"):
                b.put_list("profile_config.communication.signature_phrases", [_unquote(value) for value in group.values],
                           item_limit=160, max_items=50, excerpt=group.excerpt, section=section, confidence="high")
            elif label.startswith("avoid"):
                short = [value for value in group.values if len(value) <= 80]
                b.put_list("style_config.avoided_phrases", short, item_limit=80, max_items=20, excerpt=group.excerpt,
                           section=section, confidence="high")
                b.put_list("profile_config.communication.unnatural_expressions", [v for v in group.values if v not in short],
                           item_limit=160, max_items=50, excerpt=group.excerpt, section=section, confidence="high")
            elif not group.label:
                b.put("profile_config.communication.industry_terminology", group.text, limit=1000, excerpt=group.excerpt, section=section)
            else:
                b.keep(section, f"{group.label}: {group.text}")
    if "languages" in lines:
        switching = []
        for group in _groups(lines["languages"]):
            if _norm(group.label or "") in ("languages", "language"):
                # Keep each language as written, e.g. "Korean (fluent)".
                names = [item.strip().rstrip(".").strip() for item in re.split(r",|\band\b", group.text)]
                names = [name for name in names if name]
                default = next((name for name in names if re.search(r"\(default\)", name, re.IGNORECASE)), None)
                code = _first_language_code(default or (names[0] if names else ""))
                if code:
                    b.put("draft.preferred_language", code, limit=40, excerpt=group.excerpt, section=section)
                others = [name for name in names if name != default]
                b.put_list("profile_config.communication.other_languages", others, item_limit=160, max_items=50,
                           excerpt=group.excerpt, section=section, confidence="high")
            else:
                switching.append(group.text if not group.label else f"{group.label}: {group.text}")
        if switching:
            text = "\n".join(switching)
            b.put("profile_config.communication.language_switching", text, limit=1000, excerpt=text, section=section)
    if "behaviors" in lines:
        mapping = (("uncertain", "uncertainty_behavior"), ("negative", "negative_reply_behavior"), ("next step", "next_step_behavior"))
        for group in _groups(lines["behaviors"]):
            label = _norm(group.label or "")
            target = next((key for prefix, key in mapping if label.startswith(prefix)), None)
            if target:
                b.put(f"profile_config.communication.{target}", group.text, limit=1500, excerpt=group.excerpt, section=section)
            else:
                b.keep(section, group.text if not group.label else f"{group.label}: {group.text}")


def _playbook(raw_lines: list[str], b: _Builder) -> None:
    section = "playbook"
    items: list[tuple[str | None, list[str]]] = []
    for raw in raw_lines:
        line = _parse_line(raw)
        if line is None:
            continue
        numbered = _NUMBERED.match(raw) is not None and line.depth <= 1
        top_label = line.label is not None and line.depth <= 1
        if numbered or top_label:
            items.append((line.label, [line.value] if line.label else [line.text]))
        elif items:
            items[-1][1].append(line.text if line.label is None else f"{line.label}: {line.value}")
        else:
            b.keep(section, line.text)
    used: dict[str, list[str]] = {}
    for label, values in items:
        key = None
        if label:
            normalized = _norm(label)
            key = next((target for prefix, target in PLAYBOOK if prefix in normalized), None)
        text = "\n".join(value for value in values if value)
        if key:
            used.setdefault(key, []).append(text)
        else:
            b.keep(section, f"{label}: {text}" if label else text)
    for key, texts in used.items():
        text = "\n".join(texts)
        b.put(f"profile_config.playbook.{key}", text, limit=2000, excerpt=text, section=section)


_EXAMPLE_FIELDS = (
    ("scenario", "scenario"), ("channel", "channel"), ("language", "language"), ("customer message", "customer_message"),
    ("customer s message", "customer_message"), ("preferred response", "preferred_response"),
    ("my exact response", "preferred_response"), ("response", "preferred_response"),
    ("context", "context"), ("why it sounds like", "style_explanation"),
    ("status", "provenance"), ("provenance", "provenance"), ("source", "provenance"),
)
# Template questions (the same as the setup form) -> scenario stored on the example.
_QUESTIONS = {
    re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", question.lower())).strip(): (number, scenario, replies)
    for number, (question, scenario, replies) in enumerate(COMMUNICATION_QUESTIONS, start=1)
}
_QUESTION_NUMBERS = {number: (scenario, replies) for _key, (number, scenario, replies) in _QUESTIONS.items()}
_ADDITIONAL_HEADER = re.compile(r"^\s*(#{1,6}\s*)?(\*{2})?\s*(Additional (?:real )?example)\b[^*:]*\*{0,2}\s*$", re.IGNORECASE)
# Context note for a reply whose customer message was not written down. It is
# not first-touch outreach, so it does not get the outreach consent flag.
REPLY_WITHOUT_MESSAGE = "Reply to a customer; their message was not included"
_SCRIPTS = (("ko", "\uac00-\ud7a3\u1100-\u11ff\u3130-\u318f"), ("ja", "\u3040-\u30ff"), ("zh", "\u4e00-\u9fff"),
            ("ru", "\u0400-\u04ff"), ("ar", "\u0600-\u06ff"), ("hi", "\u0900-\u097f"), ("vi", "ăâđêôơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ"))
_SCRIPT_NAMES = {"ko": "Korean", "ja": "Japanese", "zh": "Chinese", "ru": "Russian", "ar": "Arabic", "hi": "Hindi", "vi": "Vietnamese"}


def _script_language(text: str) -> str | None:
    """The language a reply is visibly written in, when its script makes that unambiguous."""
    for code, characters in _SCRIPTS:
        if re.search(f"[{characters}]", text, re.IGNORECASE if code == "vi" else 0):
            return code
    return None


def _example_field(label: str | None) -> str | None:
    if not label:
        return None
    normalized = _norm(label)
    return next((key for prefix, key in _EXAMPLE_FIELDS if normalized.startswith(prefix)), None)


_TEXT_FIELDS = ("preferred_response", "customer_message", "context", "style_explanation")
_EXAMPLE_HEADER = re.compile(r"^\s*(#{1,6}\s*)?(\*{2})?\s*(Example\s+)?(\d+)(?:[.)]|\s*[—–]|\s+-)\s+(.+?)\s*\*{0,2}\s*$")


def _verbatim(raw: str) -> str:
    """A line of example text as written.

    Only a quote marker, a bullet marker and outer spaces are removed;
    numbered list items keep their numbers.
    """
    body = re.sub(r"^\s*>\s?", "", raw) if raw.lstrip().startswith(">") else raw
    body = re.sub(r"^\s*[-*•○◦▪]\s+", "", body)
    return _plain(body.strip())


def _is_example_header(raw: str, current_field: str | None, following: str | None) -> dict | None:
    """Decide whether a line starts a new example, and what kind.

    Headings, bold titles, "Example N", template questions and "Additional
    example" always do. A plain "N. text" line inside a reply is part of that
    reply (a numbered list), unless the next line starts labelled fields.
    """
    if raw.lstrip().startswith(("-", "*  ", ">", "•")):
        return None
    additional = _ADDITIONAL_HEADER.match(raw)
    if additional and (additional.group(1) or additional.group(2) or current_field not in _TEXT_FIELDS):
        return {"title": ADDITIONAL_TITLE, "kind": "additional"}
    match = _EXAMPLE_HEADER.match(raw)
    if not match:
        return None
    title = _plain(match.group(5)).strip("* ")
    question = _QUESTIONS.get(re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", title.lower())).strip())
    explicit_line = bool(match.group(1) or match.group(2)) or current_field not in _TEXT_FIELDS
    if not explicit_line and question is None:
        question = None  # a numbered line inside a reply is checked below like any list item
    elif question is None and re.search(r"\d\s*[—–]", raw) and int(match.group(4)) in _QUESTION_NUMBERS:
        question = (int(match.group(4)), *_QUESTION_NUMBERS[int(match.group(4))])
    if question is not None and (explicit_line or _norm(title) in {_norm(q) for q, _s, _r in COMMUNICATION_QUESTIONS}):
        return {"title": title, "kind": "question", "scenario": question[1], "replies": question[2]}
    if _norm(title) == _norm(ADDITIONAL_QUESTION):
        return {"title": ADDITIONAL_TITLE, "kind": "additional"}
    header = {"title": title, "kind": "numbered"}
    line = _parse_line(raw)
    if line is not None and _example_field(line.label) is not None:
        return None
    explicit = bool(match.group(1) or match.group(2) or match.group(3))
    if explicit or current_field not in _TEXT_FIELDS:
        return header
    following_line = _parse_line(following) if following else None
    labelled = following_line is not None and following_line.label is not None and following_line.text
    if labelled:
        first = re.split(r"\s+·\s+", following_line.text)[0]
        parsed = _parse_line(first)
        if parsed is not None and _example_field(parsed.label) is not None:
            return header
    return None


def _examples(raw_lines: list[str], b: _Builder) -> None:
    section = "examples"
    default_provenance = None
    blocks: list[dict] = []
    current_field = None
    lines = [raw for raw in raw_lines]
    for position, raw in enumerate(lines):
        if not raw.strip():
            if blocks and current_field in _TEXT_FIELDS and blocks[-1]["fields"].get(current_field):
                blocks[-1]["fields"][current_field].append("")
            continue
        following = next((item for item in lines[position + 1:] if item.strip()), None)
        header = _is_example_header(raw, current_field, following)
        line = _parse_line(raw)
        if header:
            blocks.append({**header, "fields": {}, "notes": []})
            current_field = None
            continue
        if line is None or _norm(line.text) in _INSTRUCTIONS:
            continue  # ListingIQ's own template guidance is not part of any example.
        if not blocks:
            if re.search(r"\billustrative\b", line.text, re.IGNORECASE):
                default_provenance = "illustrative"
            b.keep(section, line.text)
            continue
        block = blocks[-1]
        if line.quote and current_field:
            block["fields"][current_field].append(_verbatim(raw))
            continue
        pieces = [piece.strip() for piece in re.split(r"\s+·\s+", line.text)] if line.label else [line.text]
        handled = False
        if line.label is not None and line.depth <= 1:
            for piece in pieces:
                parsed = _parse_line(piece)
                key = _example_field(parsed.label if parsed else None)
                if key:
                    block["fields"].setdefault(key, [])
                    if parsed.value:
                        block["fields"][key].append(parsed.value)
                    current_field = key
                    handled = True
        if not handled:
            if current_field in _TEXT_FIELDS:
                block["fields"][current_field].append(_verbatim(raw))
            else:
                block["notes"].append(line.text if line.label is None else f"{line.label}: {line.value}")
    for index, block in enumerate(blocks, start=1):
        fields = {key: "\n".join(values).strip() for key, values in block["fields"].items()}
        notes = "\n".join(block["notes"])
        if not any(fields.values()) and not notes:
            continue  # An example block left blank in the template.
        response = _unquote(fields.get("preferred_response", ""))
        if not response:
            kept = [f"{label}: {value}" for label, value in fields.items() if value] + ([notes] if notes else [])
            b.keep(section, f"Example {index} ({block['title']}) has no preferred response" + (": " + "\n".join(kept) if kept else ""))
            b.questions.append(f"Example {index} ({block['title']}) has no preferred response; add the reply you would send")
            continue
        customer = _unquote(fields.get("customer_message", ""))
        if re.match(r"^\(?\s*(none|n/?a)\b", customer, re.IGNORECASE):
            customer = ""
        flags = []
        channel_text = fields.get("channel", "").lower()
        channels = [name for name in ("sms", "email") if name in channel_text]
        if not channels:
            flags.append("Channel was not stated in the document; stored as SMS until you confirm it")
            channels = ["sms"]
        elif len(channels) > 1:
            flags.append(f"The document lists several channels ({fields.get('channel')}); stored as {channels[0].upper()}")
        context = fields.get("context", "")
        language_text = fields.get("language", "").strip()
        language = _first_language_code(language_text) or (language_text.lower()[:8] if re.fullmatch(r"[A-Za-z]{2,8}", language_text) else "")
        if not language:
            detected = _script_language(response) if not language_text else None
            if detected:
                flags.append(f"Language was not stated; the reply is written in {_SCRIPT_NAMES[detected]}. Confirm the language")
                language = detected
            else:
                flags.append(
                    "Language was not stated in the document; stored as English until you confirm it" if not language_text
                    else f"Language '{language_text[:60]}' was not recognized; stored as English until you confirm it"
                )
                language = "en"
        status = _norm(fields.get("provenance", "") or (default_provenance or ""))
        if status.startswith("authentic"):
            provenance = "authentic"
        elif "illustrative" in status:
            provenance = "illustrative"
        elif "user written" in status or "user_written" in status or status.startswith("written"):
            provenance = "user_written"
        else:
            # Never presented as real: unknown sources stay illustrative until the owner decides.
            provenance = "illustrative"
            flags.append("The document does not say whether this example is authentic or illustrative; confirm its source")
        scenario_text = fields.get("scenario", "")
        scenario = block["title"][:120]
        if block["kind"] == "question":
            # The owner answered one of the form's own questions.
            scenario = block["scenario"]
            notes_for_question = [f"Answer to: {block['title']}"]
            if block["replies"] and not customer:
                notes_for_question.append(REPLY_WITHOUT_MESSAGE)
            context = "\n".join(notes_for_question + ([f"Scenario: {scenario_text}"] if scenario_text else []) + ([context] if context else []))
        elif block["kind"] == "additional":
            if scenario_text:
                scenario = scenario_text[:120]
                if len(scenario_text) > 120:
                    context = f"Scenario: {scenario_text}" + (f"\n{context}" if context else "")
            else:
                flags.append("The scenario was not named in the document; name it before approving")
        elif scenario_text:
            context = f"Scenario: {scenario_text}" + (f"\n{context}" if context else "")
        if notes:
            context = (context + "\n" if context else "") + notes
        if block["kind"] == "numbered" and len(block["title"]) > 120:
            b.keep(section, f"Example {index} title: {block['title']}")
        style_explanation = fields.get("style_explanation", "")
        if len(context) > 3000 or len(style_explanation) > 3000 or len(response) > 10000 or len(customer) > 5000:
            # Too long for an example: keep every part for the owner instead of cutting it.
            b.keep(section, f"Example {index} ({block['title']}) is longer than the example fields allow and was not imported:\n"
                   f"Customer message: {customer}\nPreferred response: {response}\nContext: {context}\nWhy: {style_explanation}")
            b.questions.append(f"Example {index} ({block['title']}) is too long to import; shorten it and add it in the form")
            continue
        b.examples.append({
            "scenario": scenario, "channel": channels[0], "language": language, "customer_message": customer,
            "preferred_response": response, "context": context, "style_explanation": style_explanation,
            "provenance": provenance, "review_flags": flags,
        })
        b.evidence.append({"field_path": f"examples[{len(b.examples) - 1}]", "source_excerpt": response[:500],
                           "confidence": "high", "needs_owner_confirmation": False})


def _knowledge_section(section: str, raw_lines: list[str], b: _Builder) -> None:
    """Keep each fact with its field label, e.g. "Track record: [Residential | Self-reported] ..."."""
    label = None
    for raw in raw_lines:
        line = _parse_line(raw)
        if line is None or _norm(line.text) in _INSTRUCTIONS:
            continue
        if line.label is not None and line.depth <= 1:
            label = line.label
            value = line.value
        else:
            value = line.text
        if not value:
            continue
        if label and _norm(label).startswith("signature"):
            # Style guidance, not knowledge: merged into the signature phrases (no duplicates), never kept for RAG.
            b.put_list("profile_config.communication.signature_phrases", [_unquote(value)], item_limit=160,
                       max_items=50, excerpt=value, section="communication", confidence="high")
            continue
        b.keep(section, f"{label}: {value}" if label else value)


def parse_document(text: str) -> tuple[PersonaExtractionContract | None, dict]:
    """Return the mapped contract and a summary, or (None, summary) when the
    document does not follow the template closely enough to read locally."""
    sections = _split_sections(text.replace("\r\n", "\n"))
    recognized = [key for key in SECTIONS if key in sections]
    builder = _Builder()
    for line in sections.get("preamble", []):
        parsed = _parse_line(line)
        if parsed and not parsed.text.startswith("#"):
            builder.keep("preamble", parsed.text)
    for key in ("identity", "background", "communication"):
        if key in sections:
            fields = _fields(key, sections[key], builder)
            {"identity": _identity, "background": _background, "communication": _communication}[key](fields, builder)
    if "playbook" in sections:
        _playbook(sections["playbook"], builder)
    if "examples" in sections:
        _examples(sections["examples"], builder)
    for key in KNOWLEDGE_SECTIONS:
        if key in sections:
            _knowledge_section(key, sections[key], builder)
    for key, lines in sections.items():
        if key.startswith("other:"):
            for line in lines:
                parsed = _parse_line(line)
                if parsed:
                    builder.keep(key, parsed.text if parsed.label is None else f"{parsed.label}: {parsed.value}")
    for key in SECTIONS:
        if key not in sections:
            builder.questions.append(f"The '{SECTION_TITLES[key]}' section was not found in the document")
    _narrative_claims(builder)
    summary = {
        "sections_recognized": recognized, "fields_mapped": builder.mapped_fields,
        "examples": len(builder.examples), "unmapped_details": len(builder.unmapped),
    }
    if len(recognized) < 3 or builder.mapped_fields < 5:
        return None, summary
    claims, unmapped, questions = _within_limits(builder)
    contract = PersonaExtractionContract.parse_obj({
        "draft": builder.draft,
        "style_config": builder.style,
        "profile_config": {key: value for key, value in builder.profile.items() if value},
        "examples": builder.examples,
        # Evidence is a pointer back to the source, not content; the cap loses no document text.
        "evidence": builder.evidence[:200],
        "claims": claims,
        "unmapped_details": unmapped,
        "missing_questions": questions,
        "contradictions": [],
        "unsupported_claims": [],
    })
    summary.update(claims=len(claims), unmapped_details=len(unmapped))
    return contract, summary


def _narrative_claims(b: _Builder) -> None:
    """Record achievement-like statements from narrative fields as unverified claims.

    The text stays where it was mapped; the claim only makes sure the owner
    reviews it instead of it being treated as established fact.
    """
    sources = [
        ("draft.biography", b.draft.get("biography", "")),
        ("profile_config.identity.brand_identity", b.profile.get("identity", {}).get("brand_identity", "")),
        ("profile_config.identity.professional_intro", b.profile.get("identity", {}).get("professional_intro", "")),
    ]
    for path in ("differentiators", "industry_expertise"):
        for item in b.profile.get("background", {}).get(path, []):
            sources.append((f"profile_config.background.{path}", item))
    known = {claim["statement"] for claim in b.claims}
    for path, text in sources:
        for sentence in _sentences(text.replace("\n", " ")) if text else []:
            if _CLAIM_WORDS.search(sentence) and sentence[:500] not in known:
                b.claim(sentence, path)
                known.add(sentence[:500])


_MAX_RECORDS = 100


def _within_limits(b: _Builder) -> tuple[list[dict], list[dict], list[str]]:
    """Fit claims, unmapped details and questions into the contract limits.

    Nothing is dropped silently: overflowing claims move to unmapped details,
    and anything still over the limit is replaced by an explicit notice. The
    full document text is always stored with the import job.
    """
    claims, unmapped = list(b.claims), list(b.unmapped)
    if len(claims) > _MAX_RECORDS:
        for claim in claims[_MAX_RECORDS:]:
            unmapped.append({"section": "Claims", "detail": f"Unverified claim: {claim['statement']}"[:1000]})
        claims = claims[:_MAX_RECORDS]
    questions = list(dict.fromkeys(question[:500] for question in b.questions))
    if len(unmapped) > _MAX_RECORDS:
        hidden = unmapped[_MAX_RECORDS - 1:]
        names = ", ".join(dict.fromkeys(item["section"] for item in hidden))
        unmapped = unmapped[:_MAX_RECORDS - 1] + [{
            "section": "Import notice",
            "detail": (f"{len(hidden)} more unrecognized details (from: {names}) could not be listed here. "
                       "They remain in the uploaded document; review it and add anything important in the form.")[:1000],
        }]
        questions.append(f"{len(hidden)} unrecognized details were too many to list; review the original document")
    if len(questions) > _MAX_RECORDS:
        hidden_questions = len(questions) - (_MAX_RECORDS - 1)
        questions = questions[:_MAX_RECORDS - 1] + [f"{hidden_questions} more questions were not listed; review the original document"]
    return claims, unmapped, questions
