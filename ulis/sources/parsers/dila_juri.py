"""Parser for French court decisions from the DILA case-law open data (CONSTIT, CASS, INCA, JADE).

One XML file per decision, in three families (``TEXTE_JURI_CONSTIT``, ``TEXTE_JURI_JUDI``,
``TEXTE_JURI_ADMIN``) with the same skeleton:

* ``META/META_COMMUN`` (identifier, nature) and ``META/META_SPEC/META_JURI`` (title, date, court, number, solution);
* a family block: ``META_JURI_CONSTIT`` (ECLI, NOR, official journal), ``META_JURI_JUDI`` (case numbers, chamber,
  decision appealed, publication in the Bulletin, ECLI) or ``META_JURI_ADMIN`` (chamber, type of appeal, Lebon);
* ``TEXTE/BLOC_TEXTUEL/CONTENU`` (the decision, lines separated by ``<br/>``), ``TEXTE/SOMMAIRE`` and
  ``TEXTE/CITATION_JP`` (headnotes and related decisions of published decisions) and ``LIENS`` (the Cour de
  cassation's list of texts applied).

What a judgment becomes:

* **work** ``/fr/juri/<id>`` (``CONSTEXT...``, ``JURITEXT...``, ``CETATEXT...``), one expression dated at the decision.
  The court is mapped from ``JURIDICTION`` (Conseil constitutionnel, Cour de cassation, Conseil d'Etat, Tribunal des
  conflits, ``CAA de ...``, ``TA de ...``); the case name is the publisher's ``TITRE``; the neutral citation is the ECLI
  when the publisher gives one; the docket is the case number(s) (``2021-960 QPC`` for the Conseil constitutionnel).
* **paragraphs** (exact source text, whitespace runs collapsed). First the headnotes (``SOMMAIRE``: ``SCT``/``ANA``) and
  the related decisions (``CITATION_JP``). Then the decision: the text before the first numbered paragraph is one
  ``header`` unit (or one unit per block of lines when no paragraph is numbered); each numbered paragraph (``1. ...``,
  numbers in sequence; the number is split off into ``num``) is a ``considerations`` unit with its continuation lines;
  everything from the operative part ("Décide", "PAR CES MOTIFS", "Article 1er") to the end is one ``decision`` unit.
* **references** (citation links, never guessed): articles of the French codes, the Constitution and laws that the
  decision names with a strict pattern (``articles 1569, 1570 du code civil``), or that the Cour de cassation lists in
  its ``LIENS`` (marked ``source_markup``: the court's own list, read with the same strict pattern). A reference is made
  only when the article number is directly followed by the name of a text in the tables of ``fr_texts``; anything else
  (articles of several texts in one phrase, "dudit code", earlier codes) is left unlinked.

Personal data: the publisher pseudonymises natural persons (CNIL recommendation of 29 November 2001). ULIS keeps the
text as published and stores no name of judges, lawyers or parties outside it (``PRESIDENT``, ``RAPPORTEUR``,
``AVOCATS``, ``DEMANDEUR`` and ``DEFENDEUR`` are not read).
"""

from __future__ import annotations

import re
from datetime import date
from xml.etree.ElementTree import Element  # noqa: S405 - type only; parsing uses defusedxml

from defusedxml.ElementTree import fromstring

from ulis.sources.normalized import ParsedDocument, ParsedJudgmentMeta, ParsedParagraph, ParsedRef
from ulis.sources.parsers import fr_texts

PARSER_ID = "dila_juri_xml"
PARSER_VERSION = "1.0.0"
FAMILIES = {"TEXTE_JURI_CONSTIT": "constit", "TEXTE_JURI_JUDI": "judi", "TEXTE_JURI_ADMIN": "admin"}
BLOCK_TAGS = {"p", "div", "li", "tr", "ul", "ol", "table", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6", "pre"}
NUMBERED = re.compile(r"^(?P<num>\d{1,3})\s*\.\s+(?P<rest>\S.*)$")
OPERATIVE = re.compile(
    r"^(?:D\s?[ÉE]\s?C\s?I\s?D\s?E|DÉCIDE|Décide|PAR CES MOTIFS|Par ces motifs|DISPOSITIF|Dispositif|"
    r"Article (?:1er|premier|1)\s*:)",
)
MAX_PARAGRAPH = 2_000_000


def work_uri(ident: str) -> str:
    return f"/fr/juri/{ident}"


def court_for(juridiction: str) -> tuple[str, str | None]:
    """``(court id, place)`` from the publisher's ``JURIDICTION``."""
    name = juridiction.strip()
    low = name.lower().replace("’", "'")
    if low == "conseil constitutionnel":
        return "FR-CC", None
    if low == "cour de cassation":
        return "FR-CCASS", None
    if low in ("conseil d'etat", "conseil d'état"):
        return "FR-CE", None
    if low == "tribunal des conflits":
        return "FR-TC", None
    caa = re.match(r"^CAA de (.+)$", name, re.I) or re.match(r"^Cour administrative d'appel de (.+)$", name, re.I)
    if caa:
        return "FR-CAA", caa.group(1).strip()
    ta = re.match(r"^TA (?:de |d')(.+)$", name, re.I) or re.match(
        r"^Tribunal administratif (?:de |d')(.+)$", name, re.I
    )
    if ta:
        return "FR-TA", ta.group(1).strip()
    raise ValueError(f"unknown court {juridiction!r}")


def _text(root: Element, path: str) -> str | None:
    el = root.find(path)
    return ((el.text or "").strip() or None) if el is not None else None


def render_lines(el: Element | None) -> list[str]:
    """Lines of a ``CONTENU`` element (``<br/>`` and block tags end a line); a blank line is ``""``, runs collapsed."""
    if el is None:
        return []
    out: list[str] = []

    def walk(node: Element) -> None:
        tag = node.tag.lower() if isinstance(node.tag, str) else ""
        if tag == "br" or tag in BLOCK_TAGS:
            out.append("\n")
        out.append(node.text or "")
        for child in node:
            walk(child)
            out.append(child.tail or "")
        if tag in BLOCK_TAGS:
            out.append("\n")

    walk(el)
    lines: list[str] = []
    for raw in "".join(out).split("\n"):
        line = re.sub(r"[ \t\r\f\v]+", " ", raw).strip()
        if line or (lines and lines[-1] != ""):
            lines.append(line)
    while lines and lines[0] == "":
        lines.pop(0)
    while lines and lines[-1] == "":
        lines.pop()
    return lines


def _plain(el: Element | None) -> str:
    return "\n".join(line for line in render_lines(el) if line)


def _refs(text: str, on: date | None) -> list[ParsedRef]:
    return [ParsedRef(r.href, r.text, "cites", "pattern") for r in fr_texts.statute_refs(text, on)]


def _label_refs(labels: list[str], on: date | None) -> list[ParsedRef]:
    return [
        ParsedRef(r.href, r.text, "cites", "source_markup")
        for label in labels
        for r in fr_texts.statute_refs(label, on)
    ]


def _blocks(chunk: list[str]) -> list[str]:
    """Blocks of consecutive non-blank lines."""
    out: list[str] = []
    current: list[str] = []
    for line in chunk:
        if line:
            current.append(line)
        elif current:
            out.append("\n".join(current))
            current = []
    if current:
        out.append("\n".join(current))
    return out


def _units(lines: list[str]) -> list[tuple[str, str | None, str]]:
    """Split the decision into ``(part, number, text)`` units (see the module docstring)."""
    # numbers must run 1, 2, 3...: a line such as "5. mai" in the middle of a paragraph is not a paragraph
    starts: dict[int, tuple[str, str]] = {}
    for i, line in enumerate(lines):
        match = NUMBERED.match(line)
        if match and int(match["num"]) == len(starts) + 1:
            starts[i] = (match["num"], match["rest"])
    operative = next(
        (i for i, line in enumerate(lines) if OPERATIVE.match(line) and (not starts or i > min(starts))), None
    )
    end_body = operative if operative is not None else len(lines)
    units: list[tuple[str, str | None, str]] = []
    if starts:
        first = min(starts)
        units.append(("header", None, "\n".join(line for line in lines[:first] if line)))
        number: str | None = None
        body: list[str] = []
        for i in range(first, end_body):
            if i in starts:
                if number is not None:
                    units.append(("considerations", number, "\n".join(x for x in body if x)))
                number, body = starts[i][0], [starts[i][1]]
            else:
                body.append(lines[i])
        if number is not None:
            units.append(("considerations", number, "\n".join(x for x in body if x)))
    else:
        units.extend(("header" if i == 0 else "text", None, block) for i, block in enumerate(_blocks(lines[:end_body])))
    if operative is not None:
        units.append(("decision", None, "\n".join(line for line in lines[operative:] if line)))
    return [u for u in units if u[2]]


def parse_dila_juri(content: bytes) -> list[ParsedDocument]:
    root = fromstring(content, forbid_dtd=True)
    family = FAMILIES.get(root.tag)
    if family is None:
        raise ValueError(f"unexpected root element {root.tag!r}")
    ident = _text(root, "META/META_COMMUN/ID")
    if not ident or not re.fullmatch(r"[A-Z]{8}\d{12}", ident):
        raise ValueError("the decision has no valid identifier")
    nature = _text(root, "META/META_COMMUN/NATURE")
    meta = root.find("META/META_SPEC/META_JURI")
    juridiction = _text(root, "META/META_SPEC/META_JURI/JURIDICTION")
    title = _text(root, "META/META_SPEC/META_JURI/TITRE")
    raw_date = _text(root, "META/META_SPEC/META_JURI/DATE_DEC")
    if meta is None or not juridiction or not title or not raw_date:
        raise ValueError(f"{ident}: court, title or date missing")
    try:
        decided = date.fromisoformat(raw_date)
    except ValueError as exc:
        raise ValueError(f"{ident}: invalid decision date {raw_date!r}") from exc
    court_id, place = court_for(juridiction)
    number = _text(root, "META/META_SPEC/META_JURI/NUMERO")
    solution = _text(root, "META/META_SPEC/META_JURI/SOLUTION")
    ext: dict[str, object] = {
        "dila_id": ident,
        "family": family,
        "nature": nature,
        "juridiction": juridiction,
        "solution": solution,
        "place": place,
    }
    if family == "constit":
        ecli = _text(root, "META/META_SPEC/META_JURI_CONSTIT/ECLI")
        qualified = _text(root, "META/META_SPEC/META_JURI_CONSTIT/NATURE_QUALIFIEE")
        docket = f"{number} {nature}" if number and nature else number
        ext.update(
            nature_qualified=qualified,
            nor=_text(root, "META/META_SPEC/META_JURI_CONSTIT/NOR"),
            journal=_text(root, "META/META_SPEC/META_JURI_CONSTIT/TITRE_JO"),
        )
    elif family == "judi":
        ecli = _text(root, "META/META_SPEC/META_JURI_JUDI/ECLI")
        cases = [
            (e.text or "").strip()
            for e in root.iterfind("META/META_SPEC/META_JURI_JUDI/NUMEROS_AFFAIRES/NUMERO_AFFAIRE")
        ]
        docket = "; ".join(c for c in cases if c) or None
        published = root.find("META/META_SPEC/META_JURI_JUDI/PUBLI_BULL")
        ext.update(
            formation=_text(root, "META/META_SPEC/META_JURI_JUDI/FORMATION"),
            published_in_bulletin=(published.get("publie") == "oui") if published is not None else None,
            appealed_court=_text(root, "META/META_SPEC/META_JURI_JUDI/FORM_DEC_ATT"),
            appealed_date=_text(root, "META/META_SPEC/META_JURI_JUDI/DATE_DEC_ATT"),
        )
    else:
        ecli = _text(root, "META/META_SPEC/META_JURI_ADMIN/ECLI")
        docket = number
        ext.update(
            formation=_text(root, "META/META_SPEC/META_JURI_ADMIN/FORMATION"),
            appeal_type=_text(root, "META/META_SPEC/META_JURI_ADMIN/TYPE_REC"),
            published_in_recueil=_text(root, "META/META_SPEC/META_JURI_ADMIN/PUBLI_RECUEIL"),
        )
    ext["ecli"] = ecli

    paragraphs: list[ParsedParagraph] = []
    labels = [(link.text or "").strip() for link in root.iterfind("LIENS/LIEN") if (link.text or "").strip()]
    pending_labels = _label_refs(labels, decided)

    def add(part: str, eid: str, num: str | None, text: str) -> None:
        if len(text) > MAX_PARAGRAPH:
            raise ValueError(f"{ident}: a paragraph is too long")
        refs = [*pending_labels, *_refs(text, decided)] if not paragraphs else _refs(text, decided)
        pending_labels.clear()
        unique = list({r.href: r for r in reversed(refs)}.values())[::-1]  # one link per target, the first wins
        paragraphs.append(ParsedParagraph(eid, num, part, text, len(paragraphs), unique))

    for i, sct in enumerate(root.iterfind("TEXTE/SOMMAIRE/SCT"), start=1):
        if sct.get("TYPE") == "PRINCIPAL" and (text := _plain(sct)):
            add("headnote", f"hn-sct-{i}", None, text)
    for i, ana in enumerate(root.iterfind("TEXTE/SOMMAIRE/ANA"), start=1):
        if text := _plain(ana):
            add("headnote", f"hn-ana-{i}", None, text)
    if text := _plain(root.find("TEXTE/CITATION_JP/CONTENU")):
        add("references", "ref-1", None, text)
    counters: dict[str, int] = {}
    for part, num, text in _units(render_lines(root.find("TEXTE/BLOC_TEXTUEL/CONTENU"))):
        if num is not None:
            add(part, f"para-{num}", num, text)
        else:
            counters[part] = counters.get(part, 0) + 1
            add(part, f"{part}-{counters[part]}", None, text)
    if not any(p.part in ("considerations", "header", "text", "decision") for p in paragraphs):
        raise ValueError(f"{ident}: the decision has no text")

    uri = work_uri(ident)
    return [
        ParsedDocument(
            doc_kind="judgment",
            instrument_type="judgment",
            jurisdiction_id="FR",
            work_uri=uri,
            expression_uri=f"{uri}/fra@{decided.isoformat()}",
            work_title=title,
            title=title,
            short_title=None,
            number=docket,
            work_date=decided,
            language="fr",
            authoritative=False,  # pseudonymised publication; the court's signed decision is the original
            text_origin="official",
            consolidation_kind="unknown",
            version_date=decided,
            currency_date=None,
            extent=["FR"],
            ext_module="fr",
            ext={k: v for k, v in ext.items() if v not in (None, "")},
            judgment=ParsedJudgmentMeta(
                court_id=court_id,
                case_name=title,
                neutral_citation=ecli,
                decision_date=decided,
                docket=docket,
            ),
            paragraphs=paragraphs,
        )
    ]
