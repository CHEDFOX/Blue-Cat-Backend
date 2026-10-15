"""Parser for the Texas statutes (Texas Legislative Council): the HTML zip of one code.

Input: the bundle (see :mod:`ulis.sources.us_state_bundle`) of ``Zips/<CODE>.htm.zip`` from the "Download
Statutes" page of https://statutes.capitol.texas.gov. The zip holds one HTML file per chapter (``pe.19.htm``);
a code is one instrument, its sections (``Sec.``) or articles (``Art.``) are the provisions. Format notes:

* the files are flat lists of ``<p>``: centred paragraphs are the headings (``TITLE 5. ...``, ``CHAPTER 19. ...``,
  ``SUBCHAPTER A. ...``) and, when not upper case, the Council's notes in the text ("Text of section effective
  until ...", "For text of section as added by ..., see other Sec. 32.56."); a section starts with a paragraph
  whose first element is a bold link ``Sec. 19.02.  MURDER.`` followed by the first words of the text; later
  paragraphs are indented text. After the text come the source credit lines (``Acts 1973, 63rd Leg., ...``,
  ``Amended by:`` and the indented acts below it).
* text is the paragraphs as printed (runs of spaces collapse); the credit lines and the Council's notes go in the
  provision note. A section whose credit lines end with ``Repealed by ...`` is recorded as repealed with no text.
* A number printed more than once in a code (conflicting amendments published side by side, with the Council's
  note) gets a numbered eId and the note "Ambiguous pinpoint"; nothing is merged.
* Zip members that are stale copies (``*-old.htm``, ``*_old.htm``, ``* - Copy.htm``) are not ingested.
* The files carry no edition date and no start dates: the document is dated by its retrieval date and attested as
  of that date (``validity_basis = "attested_at_edition"``); the Council's site states what session the statutes
  are current through. Cross references are links (``GetStatute.aspx?Code=GV&Value=30.00010``) and become
  citation links.
"""

from __future__ import annotations

import io
import re
import zipfile
from datetime import date
from urllib.parse import parse_qs, urlsplit

from ulis.sources.normalized import ParsedDocument, ParsedRef
from ulis.sources.parsers.us_state_common import (
    Node,
    SectionDraft,
    StateParseError,
    build_provisions,
    parse_html,
    raw_text,
    state_document,
    ws,
)
from ulis.sources.us_state_bundle import read_bundle

PARSER_ID = "us_tx_statutes_zip"
PARSER_VERSION = "1.0.0"
JURISDICTION = "US-TX"
MODULE = "us_tx"
MAX_UNCOMPRESSED = 400 * 1024 * 1024

CODES = {
    "AG": "Agriculture Code",
    "AL": "Alcoholic Beverage Code",
    "WL": "Auxiliary Water Laws",
    "BC": "Business & Commerce Code",
    "BO": "Business Organizations Code",
    "CP": "Civil Practice and Remedies Code",
    "CR": "Code of Criminal Procedure",
    "ED": "Education Code",
    "EL": "Election Code",
    "ES": "Estates Code",
    "FA": "Family Code",
    "FI": "Finance Code",
    "GV": "Government Code",
    "HS": "Health and Safety Code",
    "HR": "Human Resources Code",
    "IN": "Insurance Code",
    "I1": "Insurance Code - Not Codified",
    "LA": "Labor Code",
    "LG": "Local Government Code",
    "NR": "Natural Resources Code",
    "OC": "Occupations Code",
    "PW": "Parks and Wildlife Code",
    "PE": "Penal Code",
    "PR": "Property Code",
    "SD": "Special District Local Laws Code",
    "TX": "Tax Code",
    "TN": "Transportation Code",
    "UT": "Utilities Code",
    "WA": "Water Code",
    "CV": "Vernon's Civil Statutes",
}
# Codes whose units are "Art." (the others print "Sec."); used for the target of a cross reference.
ARTICLE_CODES = frozenset({"CR", "CV", "WL", "I1"})

STRUCTURE = re.compile(r"^(TITLE|SUBTITLE|CHAPTER|SUBCHAPTER|ARTICLE|PART|DIVISION)\b")
HEAD = re.compile(r"^(Sec\.|Art\.)\s+(\S+?)\.(?:\s+(.*))?$")
CREDIT = re.compile(
    r"^(Acts\s+\d{4}|Added by|Amended by|Renumbered|Redesignated|Transferred|Re-?enacted|Repealed by|Expired|"
    r"Relettered|Secs?\.\s+[0-9A-Za-z.()\s,-]+(amended|added|repealed|renumbered)|Subsecs?\.|Section heading|"
    r"Sec\.\s*\(|Originally)\b"
)
RANK = {"TITLE": 0, "SUBTITLE": 1, "CHAPTER": 2, "ARTICLE": 3, "PART": 3, "DIVISION": 3, "SUBCHAPTER": 4}
REPEAL = re.compile(r"^(Repealed by|Expired)\b")
EFFECTIVE = re.compile(r"\beff\.?\s+([A-Z][a-z]+)\.?\s+(\d{1,2}),\s+(\d{4})")
MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}  # fmt: skip


def repeal_date(line: str) -> date | None:
    """The date a repeal line says it takes effect ("... Sec. 3.01, eff. April 1, 2027."), if it gives one."""
    m = EFFECTIVE.search(line)
    if m is None or m.group(1)[:3].lower() not in MONTHS:
        return None
    try:
        return date(int(m.group(3)), MONTHS[m.group(1)[:3].lower()], int(m.group(2)))
    except ValueError:
        return None


STALE_MEMBER = re.compile(r"(?i)(^|[^a-z])(old|copy)([^a-z]|$)")


def work_uri(code: str) -> str:
    return f"/us-tx/statutes/{code}"


def unit_eid(code: str, number: str) -> str:
    return f"{'art' if code in ARTICLE_CODES else 'sec'}-{re.sub(r'[^A-Za-z0-9.-]', '_', number)}"


def resolve_uri(uri: str) -> tuple[str, str | None] | None:
    """Texas statute links -> (work URI, eId).

    ``https://statutes.capitol.texas.gov/GetStatute.aspx?Code=GV&Value=30.00010`` and
    ``https://statutes.capitol.texas.gov/Docs/GV/htm/GV.30.htm#30.00010`` -> (``/us-tx/statutes/GV``, ``sec-30.00010``).
    """
    parts = urlsplit(uri)
    if parts.netloc.lower() not in ("statutes.capitol.texas.gov", "www.statutes.capitol.texas.gov"):
        return None
    if parts.path.lower().endswith("/getstatute.aspx"):
        query = {k.lower(): v[0] for k, v in parse_qs(parts.query).items()}
        code, value = query.get("code", ""), query.get("value", "")
    else:
        m = re.fullmatch(r"/Docs/([A-Z0-9]{2})/htm/\1\.[0-9A-Za-z._-]+\.htm", parts.path)
        code, value = (m.group(1), parts.fragment) if m else ("", "")
    if code not in CODES or not re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.-]*", value or ""):
        return None
    return work_uri(code), unit_eid(code, value)


def _ref(code: str, a: Node) -> ParsedRef | None:
    target = resolve_uri(a.attrs.get("href", ""))
    if target is None:
        return None
    work, eid = target
    return ParsedRef(href=f"{work}/~{eid}", text=ws(raw_text(a)), relation="cites")


def _members(code: str, names: list[str]) -> list[str]:
    pattern = re.compile(rf"(?i)^{code}\.([0-9]+[0-9a-z]*(?:[._-][0-9a-z]+)*)\.htm$")
    found = [n for n in names if pattern.match(n) and not STALE_MEMBER.search(n[len(code) :])]

    def key(name: str) -> list[tuple[int, int, str]]:
        chapter = pattern.match(name).group(1)  # type: ignore[union-attr]
        return [(0, int(p), "") if p.isdigit() else (1, 0, p) for p in re.findall(r"\d+|[a-zA-Z]+|[^\w]+", chapter)]

    return sorted(found, key=key)


def _is_section_link(a: Node) -> bool:
    return (
        bool(a.attrs.get("href"))
        and "font-weight:bold" in a.attrs.get("style", "").replace(" ", "")
        and ws(raw_text(a)).startswith(("Sec.", "Art."))
    )


def _parse_chapter(text: str, member: str, code: str, today: date) -> list[SectionDraft]:
    root = parse_html(text)
    levels: dict[int, str] = {}
    current: SectionDraft | None = None
    credit: list[str] = []
    pending: list[str] = []
    drafts: list[SectionDraft] = []

    def flush_credit() -> None:
        nonlocal credit
        if current is not None and credit:
            current.notes.append("Source credit: " + " ".join(credit))
            for line in credit:
                if not REPEAL.match(line):
                    continue
                effective = repeal_date(line)
                if effective is not None and effective > today:
                    # A repeal that takes effect later: the text is in force until then (stated by the source).
                    current.valid_to = effective
                    current.notes.append(
                        f"Repeal takes effect on {effective.isoformat()} (stated by the source); "
                        "the text applies until then."
                    )
                else:
                    current.repealed = True
        credit = []

    def close() -> None:
        nonlocal current
        if current is None:
            return
        flush_credit()
        drafts.append(current)
        current = None

    def add_refs(p: Node, skip: Node | None) -> None:
        assert current is not None
        for a in p.find_all(lambda n: n.tag == "a" and bool(n.attrs.get("href"))):
            ref = _ref(code, a)
            if a is not skip and ref is not None and all(ref.href != r.href for r in current.refs):
                current.refs.append(ref)

    def context() -> str | None:
        return "; ".join(levels[k] for k in sorted(levels)) or None

    for p in root.find_all(lambda n: n.tag == "p"):
        line = ws(raw_text(p, br=" "))
        if p.attrs.get("class") == "center":
            if not line:
                continue
            if STRUCTURE.match(line) and line == line.upper():
                close()
                rank = RANK[line.split()[0]]
                for deeper in [k for k in levels if k >= rank]:
                    del levels[deeper]
                levels[rank] = line
            elif current is not None and not credit:
                # Inside the text of a section (a caption of a form, "Text of subsection effective until ..."): it
                # stays where the Council printed it, so the versions of a subsection remain distinguishable.
                current.lines.append(line)
            elif line != line.upper():
                # After the credit lines, the Council's notes introduce the section that follows. (A caption in
                # capitals between sections - the code's name, a descriptive heading - is a heading, not law text.)
                pending.append(f"Source note: {line}")
            continue
        link = p.find(_is_section_link)
        if link is not None:
            close()
            lead = ws(raw_text(link))
            m = HEAD.match(lead)
            if m is None:
                raise StateParseError(f"{member}: unreadable section heading {lead!r}")
            whole = ws(raw_text(p, br=" "))
            rest = whole[len(lead) :].strip() if whole.startswith(lead) else ""
            current = SectionDraft(
                kind="section" if m.group(1) == "Sec." else "article",
                number=m.group(2),
                heading=m.group(3) or None,
                lines=[rest] if rest else [],
                notes=pending,
                context=context(),
            )
            pending = []
            add_refs(p, link)
            continue
        if not line or current is None:
            if line:
                pending.append(f"Source note: {line}")
            continue
        indented = "text-indent" in p.attrs.get("style", "")
        if credit and indented and not CREDIT.match(line):
            flush_credit()  # indented text that is not a credit line: the section's text goes on
        if credit or (not indented and CREDIT.match(line)):
            credit.append(line)
            continue
        current.lines.append(line)
        add_refs(p, None)
    close()
    return drafts


def parse_tx_statutes(content: bytes) -> list[ParsedDocument]:
    bundle = read_bundle(content, "TX")
    code = bundle.key
    if code not in CODES:
        raise StateParseError(f"{code!r} is not a Texas code the parser knows")
    try:
        archive = zipfile.ZipFile(io.BytesIO(bundle.body_bytes))
    except zipfile.BadZipFile as exc:
        raise StateParseError(f"{code}: not a readable zip archive: {exc}") from exc
    with archive:
        if sum(i.file_size for i in archive.infolist()) > MAX_UNCOMPRESSED:
            raise StateParseError(f"{code}: archive is larger than {MAX_UNCOMPRESSED} bytes when unpacked")
        members = _members(code, archive.namelist())
        if not members:
            raise StateParseError(f"{code}: no chapter files found in the archive")
        drafts: list[SectionDraft] = []
        for member in members:
            drafts.extend(_parse_chapter(archive.read(member).decode("utf-8-sig"), member, code, bundle.retrieved_on))
    name = CODES[code]
    return [
        state_document(
            bundle,
            jurisdiction=JURISDICTION,
            module=MODULE,
            work_uri=work_uri(code),
            work_title=name,
            title=f"Texas {name}",
            short_title=None,
            number=code,
            ext={"code": code, "code_name": name},
            provisions=build_provisions(drafts, JURISDICTION),
            edition="statutes-online",
        )
    ]


__all__ = ["CODES", "PARSER_ID", "PARSER_VERSION", "parse_tx_statutes", "resolve_uri", "work_uri"]
