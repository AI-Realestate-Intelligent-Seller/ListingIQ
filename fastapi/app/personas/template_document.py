"""The ListingIQ Persona template, as an editable Word document or Markdown.

One definition produces both formats. Its headings and labels are the ones
``template_parser`` reads, so a completed copy is mapped without any AI.
The Word file is built with the standard library only.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from datetime import datetime, timezone
from xml.sax.saxutils import escape

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
DOCX_FILENAME = "listingiq-persona-template.docx"
MARKDOWN_FILENAME = "listingiq-persona-template.md"

TITLE = "ListingIQ Personal AI Profile Template"
INTRO = (
    "Fill in each label in your own words, then upload the completed file to ListingIQ. Keep the headings and labels as "
    "they are. Write N/A with a short reason when something does not apply. Remove private customer information. "
    "Do not invent credentials, achievements or conversations."
)
COMMUNICATION_INSTRUCTION = (
    "Write the exact message you would naturally send. Use your own words, expressions, punctuation and preferred "
    "language. Do not make your responses more formal or polished just for this document. If you do not know how you "
    "would respond, leave the answer blank rather than inventing one."
)
ADDITIONAL_GUIDE = (
    "For each additional example, copy the whole Additional example block below (its heading and every label) and "
    "paste it underneath, as many times as you need. Channel is SMS or Email. Source is authentic (a real message you "
    "sent, with customer details removed)."
)
SELLING_GUIDE = (
    "Facts about how you work: pricing, property assessment, marketing, negotiation, follow-up, services and fees. "
    "Write what you actually do."
)
KNOWLEDGE_GUIDE = (
    "One fact per line, in this form: [Category | Source | Status] Fact. Category is Residential, Commercial, "
    "Business sales, Credentials, Brokerage and team, Services, Process or FAQ. Status is Verified (name the record), "
    "Self-reported or Needs confirmation. Never present business or commercial sales as residential home sales. "
    "Signature phrases are style, not evidence: put facts here, not in the phrases."
)
# Template guidance that the parser ignores, so it never appears as imported content.
INSTRUCTION_LINES = (INTRO, COMMUNICATION_INSTRUCTION, ADDITIONAL_GUIDE, SELLING_GUIDE, KNOWLEDGE_GUIDE)

# The same questions, in the same order, as the "How I Communicate" step of the
# setup form. (question, scenario stored on the example, answers a customer?)
# The scenario names match the form, so imported answers land on the same prompts.
COMMUNICATION_QUESTIONS: tuple[tuple[str, str, bool], ...] = (
    ("How do you introduce yourself to a new client?", "Introducing myself to a new client", False),
    ("How would you reply when someone asks about their property's value?", "Property value question", True),
    ("What would you say to someone who is interested but hesitant?", "Interested but hesitant", True),
    ("How would you respond to a commission or pricing objection?", "Commission or pricing objection", True),
    ("How would you follow up with someone who hasn't replied?", "Following up after no reply", False),
    ("How would you respond to an upset customer?", "Upset customer", True),
    ("How would you suggest an appointment?", "Suggesting an appointment", True),
)
ADDITIONAL_QUESTION = "Add another real example of how you naturally communicate."
ADDITIONAL_TITLE = "Additional example"
RESPONSE_LABEL = "Response"
ADDITIONAL_FIELDS = ("Scenario", "Customer's message", "My exact response", "Channel", "Language", "Source", "Context")


def question_heading(number: int, question: str) -> str:
    return f"{number:02d} — {question}"

# (label, sub-labels). A label without sub-labels is answered on the same line.
# Labels start with the prefixes ``template_parser`` reads; keep them when editing.
SECTIONS: tuple[tuple[str, tuple[tuple[str, tuple[str, ...]], ...]], ...] = (
    ("Who I Am", (
        ("Full name and preferred display name", ()),
        ("Professional role and title", ()),
        ("License number, jurisdiction, status and current brokerage (or N/A reason)",
         ("Number", "Jurisdiction", "Status", "Brokerage")),
        ("Public professional email and phone", ()),
        ("Professional introduction and brand identity", ()),
    )),
    ("Professional Background", (
        ("Biography, experience, specialties, service areas, and target audiences",
         ("Biography", "Residential experience (years, homes sold, property types; only what you can support)",
          "Commercial or business-sale experience (kept separate from residential)",
          "Specialties", "Service areas", "Target audiences")),
        ("Industry expertise, differentiators, achievements, and claim status",
         ("Achievements (add self-reported after each one that is not independently verified)", "Differentiators")),
    )),
    ("Communication DNA", (
        ("Personality, tone, formality and conversation approach", ()),
        ("SMS and email length (for example 1-3 sentences, one question per message)", ()),
        ("Languages and language-switching preferences", ("Languages", "When I switch languages")),
        ("Vocabulary and signature phrases to use, plus expressions to avoid",
         ("Signature phrases (Use)", "Avoid (phrases, styles, filler and sales tactics)")),
        ("How I communicate uncertainty, negative replies, and next steps", ("Uncertainty", "Negative replies", "Next steps")),
    )),
    ("Conversation Playbook", (
        ("Escalate sensitive matters", ()),
        ("Hand off to a human", ()),
    )),
)
# Sub-labels whose answer is a list, written one item per line.
LIST_LABELS = frozenset({"Signature phrases (Use)", "Avoid (phrases, styles, filler and sales tactics)"})

# Factual sections after the examples. The parser keeps their content as
# source details for owner review; details the owner keeps become knowledge.
SELLING_TITLE = "Selling Approach and Services"
SELLING_FIELDS = ("Pricing", "Property assessment", "Marketing", "Negotiation", "Follow-up",
                  "Services and capabilities", "Fees and terms")
KNOWLEDGE_TITLE = "Knowledge Base"
# "Signature Phrases" here is style, not knowledge: the parser adds its lines to
# the Persona's signature phrases and never to the knowledge base.
KNOWLEDGE_FIELDS = ("Track record", "Credentials and qualifications", "Brokerage and team", "Process", "FAQ",
                    "Signature Phrases")
# The owner-edited Word template is the authoritative download; it uses the
# same headings and labels as the definitions above.
AUTHORITATIVE_DOCX = Path(__file__).with_name(DOCX_FILENAME)

def markdown_template() -> str:
    lines = [f"# {TITLE}", "", INTRO, ""]
    for heading, fields in SECTIONS:
        lines += [f"## {heading}"]
        for label, subs in fields:
            lines.append(f"- **{label}:**")
            lines += [f"  - **{sub}:**" for sub in subs]
        lines.append("")
    lines += ["## Conversation Examples", COMMUNICATION_INSTRUCTION, ""]
    for number, (question, _scenario, _reply) in enumerate(COMMUNICATION_QUESTIONS, start=1):
        lines += [f"### {question_heading(number, question)}", "", f"{RESPONSE_LABEL}:", ""]
    lines += [f"### {question_heading(len(COMMUNICATION_QUESTIONS) + 1, ADDITIONAL_QUESTION)}", "", ADDITIONAL_GUIDE, "",
              f"### {ADDITIONAL_TITLE}", ""] + [f"{name}:" for name in ADDITIONAL_FIELDS]
    for title, guide, fields in ((SELLING_TITLE, SELLING_GUIDE, SELLING_FIELDS), (KNOWLEDGE_TITLE, KNOWLEDGE_GUIDE, KNOWLEDGE_FIELDS)):
        lines += ["", f"## {title}", guide, ""] + [f"- **{name}:**" for name in fields]
    return "\n".join(lines).rstrip() + "\n"


# --- Word document -----------------------------------------------------------

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _run(text: str, *, bold: bool = False) -> str:
    if not text:
        return ""
    properties = "<w:rPr><w:b/></w:rPr>" if bold else ""
    return f'<w:r>{properties}<w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def _paragraph(*, style: str | None = None, level: int | None = None, label: str = "", text: str = "") -> str:
    properties = ""
    if style or level is not None:
        numbering = f'<w:numPr><w:ilvl w:val="{level}"/><w:numId w:val="1"/></w:numPr>' if level is not None else ""
        style_tag = f'<w:pStyle w:val="{style}"/>' if style else ""
        properties = f"<w:pPr>{style_tag}{numbering}</w:pPr>"
    runs = (_run(f"{label}:", bold=True) + _run(" " + text if text else " ")) if label else _run(text)
    return f"<w:p>{properties}{runs}</w:p>"


def _labelled_lines(label: str, value: str) -> list[str]:
    """A label paragraph; further lines of a multi-line answer are separate paragraphs, as typed in Word."""
    lines = value.split("\n") if value else [""]
    return [_paragraph(label=label, text=lines[0])] + [_paragraph(text=line) for line in lines[1:]]


def _document_xml(answers: dict[str, str] | None = None, responses: dict[int, str] | None = None,
                  additional: list[dict[str, str]] | None = None) -> str:
    """Build the body. The optional arguments pre-fill values (used to test the round trip):
    ``responses`` answers questions 1-7 by number; ``additional`` holds filled Additional example blocks."""
    answers = answers or {}
    body = [_paragraph(style="Title", text=TITLE), _paragraph(text=INTRO)]
    for heading, fields in SECTIONS:
        body.append(_paragraph(style="Heading2", text=heading))
        for label, subs in fields:
            body.append(_paragraph(style="ListParagraph", level=0, label=label, text=answers.get(label, "")))
            for sub in subs:
                value = answers.get(sub, "")
                if sub in LIST_LABELS:
                    body.append(_paragraph(style="ListParagraph", level=1, label=sub))
                    items = [item for item in value.split("\n") if item] or [""]
                    body += [_paragraph(style="ListParagraph", level=2, text=item) for item in items]
                else:
                    body.append(_paragraph(style="ListParagraph", level=1, label=sub, text=value))
    body.append(_paragraph(style="Heading2", text="Conversation Examples"))
    body.append(_paragraph(text=COMMUNICATION_INSTRUCTION))
    responses = responses or {}
    for number, (question, _scenario, _reply) in enumerate(COMMUNICATION_QUESTIONS, start=1):
        body.append(_paragraph(style="Heading3", text=question_heading(number, question)))
        body += _labelled_lines(RESPONSE_LABEL, responses.get(number, ""))
    body.append(_paragraph(style="Heading3", text=question_heading(len(COMMUNICATION_QUESTIONS) + 1, ADDITIONAL_QUESTION)))
    body.append(_paragraph(text=ADDITIONAL_GUIDE))
    for block in (additional if additional else [{}]):
        body.append(_paragraph(style="Heading3", text=ADDITIONAL_TITLE))
        for name in ADDITIONAL_FIELDS:
            body += _labelled_lines(name, block.get(name, ""))
    for title, guide, fields in ((SELLING_TITLE, SELLING_GUIDE, SELLING_FIELDS), (KNOWLEDGE_TITLE, KNOWLEDGE_GUIDE, KNOWLEDGE_FIELDS)):
        body += [_paragraph(style="Heading2", text=title), _paragraph(text=guide)]
        for name in fields:
            body.append(_paragraph(style="ListParagraph", level=0, label=name))
            items = [item for item in answers.get(name, "").split("\n") if item]
            body += [_paragraph(style="ListParagraph", level=1, text=item) for item in items]
    section = (
        '<w:sectPr><w:pgSz w:w="12240" w:h="15840"/>'
        '<w:pgMar w:top="1080" w:right="1080" w:bottom="1080" w:left="1080" w:header="720" w:footer="720" w:gutter="0"/>'
        "</w:sectPr>"
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{_W}" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<w:body>{"".join(body)}{section}</w:body></w:document>'
    )


_STYLES = f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="{_W}">
<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Malgun Gothic" w:cs="Calibri"/><w:sz w:val="22"/><w:szCs w:val="22"/><w:lang w:val="en-US" w:eastAsia="ko-KR"/></w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:after="120" w:line="276" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults>
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:spacing w:after="240"/></w:pPr><w:rPr><w:b/><w:color w:val="1F3B33"/><w:sz w:val="40"/><w:szCs w:val="40"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="360" w:after="120"/><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:b/><w:color w:val="1F5F8B"/><w:sz w:val="30"/><w:szCs w:val="30"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="240" w:after="80"/><w:outlineLvl w:val="2"/></w:pPr><w:rPr><w:b/><w:color w:val="1F3B33"/><w:sz w:val="24"/><w:szCs w:val="24"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="ListParagraph"><w:name w:val="List Paragraph"/><w:basedOn w:val="Normal"/><w:qFormat/><w:pPr><w:ind w:left="720"/><w:contextualSpacing/></w:pPr></w:style>
</w:styles>"""

_BULLETS = ("•", "◦", "▪")
_NUMBERING = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    f'<w:numbering xmlns:w="{_W}"><w:abstractNum w:abstractNumId="0"><w:multiLevelType w:val="hybridMultilevel"/>'
    + "".join(
        f'<w:lvl w:ilvl="{level}"><w:start w:val="1"/><w:numFmt w:val="bullet"/><w:lvlText w:val="{_BULLETS[level % 3]}"/>'
        f'<w:lvlJc w:val="left"/><w:pPr><w:ind w:left="{360 * (level + 1)}" w:hanging="360"/></w:pPr></w:lvl>'
        for level in range(9)
    )
    + '</w:abstractNum><w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num></w:numbering>'
)
_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
    '<Default Extension="xml" ContentType="application/xml"/>'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
    '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
    '<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml"/>'
    '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
    '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>'
    "</Types>"
)
_ROOT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
    '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
    '<Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/>'
    "</Relationships>"
)
_DOCUMENT_RELS = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
    '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/>'
    "</Relationships>"
)
_APP = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
    "<Application>ListingIQ</Application></Properties>"
)


def _core() -> str:
    # A fixed timestamp keeps the file byte-for-byte reproducible.
    created = datetime(2026, 1, 1, tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        f"<dc:title>{escape(TITLE)}</dc:title><dc:creator>ListingIQ</dc:creator>"
        f'<dcterms:created xsi:type="dcterms:W3CDTF">{created}</dcterms:created>'
        "</cp:coreProperties>"
    )


def authoritative_docx() -> bytes:
    """The owner-edited template file, or the generated one if it is missing."""
    try:
        return AUTHORITATIVE_DOCX.read_bytes()
    except OSError:
        return docx_template()


def docx_template(answers: dict[str, str] | None = None, responses: dict[int, str] | None = None,
                  additional: list[dict[str, str]] | None = None) -> bytes:
    """A valid, editable Word (.docx) package of the template."""
    buffer = io.BytesIO()
    parts = (
        ("[Content_Types].xml", _CONTENT_TYPES),
        ("_rels/.rels", _ROOT_RELS),
        ("word/document.xml", _document_xml(answers, responses, additional)),
        ("word/_rels/document.xml.rels", _DOCUMENT_RELS),
        ("word/styles.xml", _STYLES),
        ("word/numbering.xml", _NUMBERING),
        ("docProps/core.xml", _core()),
        ("docProps/app.xml", _APP),
    )
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in parts:
            info = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content.encode("utf-8"))
    return buffer.getvalue()
