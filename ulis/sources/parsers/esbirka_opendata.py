"""Parser for versions of Czech acts from the e-Sbírka open-data dumps (see ``connectors/esbirka_opendata``).

Input: one raw document built by the connector: the record of a version ("znění") of an act (``version``), the act's
metadata (``metadata``) and the fragments of that version in document order (``columns`` names the values of each row
of ``fragments``), with the fragment text and type copied from the publisher's fragment dump.

* A fragment's ELI path says what it is: ``.../par_5/odst_2/pism_a`` is the letter a) of subsection 2 of section 5;
  ``cl_`` is an article (the Constitution), ``bod_`` a numbered point. A provision's ``eid`` is its path without the
  structural containers (``par_5.odst_2.pism_a``), identical in every version of the act; ``num_path`` is
  ``["sec:5", "sub:2", "pt:a"]`` from the fragment labels (``label_number``).
* Containers (``cast_``, ``hlava_``, ``dil_``, ``oddil_``, ``pododdil_``) and headings (``Nadpis_pod`` under a container
  or a section, ``Nadpis_nad`` above a group of sections) give ``heading``/``context_heading`` only. An unnumbered
  paragraph (``Odstavec_Dc`` without a number), text continuing after a list (``Pokracovani_Text``), a table and any
  other text fragment is part of the text of the provision that contains it. An annex (``priloha_``) is one provision.
* Fragment text carries a little HTML: ``<var>`` holds the label ("(1)", "a)", "§ 5") and is dropped for numbered
  provisions, whose label is in ``num_path``; ``<a>`` is a link whose text is kept (the target is only an id, so links
  are not references); ``<sup>`` becomes superscript digits; tables become lines of cells separated by " | ". Runs of
  white space, including non-breaking spaces, become one space. Footnotes (``poznamkypodcarou``) are the ``note`` of the
  provisions that mark them with ``<sup>N</sup>)``.
* The version is one whole-instrument snapshot (``validity_basis = revision_snapshot``): provisions are dated from
  the version's effective-from date (``znění-datum-účinnosti-od``) to the day after its effective-to date, or open
  when that date is not before ``as_of``. A fragment with its own later effective date (``odlišná účinnost``) starts
  then, and is left out of a version that ends before that date (the first version of the Civil Code, 2012-03-22 to
  2013-12-31, contains the one section of it that applied before 2014). Repealed provisions are not shown by the
  consolidated texts, so they are simply absent from the later versions.
"""

from __future__ import annotations

import html
import json
import re
from datetime import date, timedelta
from typing import Any

from ulis.sources.normalized import ParsedDocument, ParsedEvent, ParsedProvision, ParseSkipped

PARSER_ID = "esbirka_opendata_json"
PARSER_VERSION = "1.0.0"
JURISDICTION = "CZ"
SUPER = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")
FORMAT = "esbirka-opendata-slice/1"
CONSTITUTIONAL = {"sb/1993/1", "sb/1993/2"}  # the Constitution and the Charter of Fundamental Rights and Freedoms
SUBTYPES = {
    "ZAKONUST": "constitutional_act",
    "ZAKON": "act",
    "OPATRSEN": "act",
    "DEKRET": "decree",
    "DEKRETUST": "decree",
    "VYHLASKA": "regulation",
    "NARIZENI": "regulation",
}
# fragment type -> (kind, num_path kind) of the provisions
NUMBERED = {
    "Clanek": ("article", "art"),
    "Clanek_novely": ("article", "art"),
    "Paragraf": ("section", "sec"),
    "Odstavec_Dc": ("subsection", "sub"),
    "Pismeno_Lb": ("paragraph", "pt"),
    "Bod_Dd": ("point", "item"),
    "Bod_novely": ("point", "item"),
}
CONTAINERS = ("Cast", "Hlava", "Dil", "Oddil", "Pododdil")
SKIPPED = {
    "Virtual_Document",
    "Virtual_Prefix",
    "Prefix",
    "Prefix_Number",
    "Prefix_Type",
    "Prefix_Author",
    "Prefix_Date",
    "Prefix_Title",
    "Virtual_Postfix",
    "Postfix",
    "Postfix_Head",
    "Postfix_Date",
    "Virtual_PPC",
}
REPEALED = re.compile(r"^\s*(?:\()?(?:zrušen[oyaé]?|vypuštěn[oyaé]?)(?:\))?\.?\s*$", re.I)
FOOTNOTE_MARK = re.compile(r"<sup>\s*([0-9]+[a-z]?)\s*</sup>\s*\)")


class EsbirkaParseError(ValueError):
    pass


def _clean(raw: str | None, keep_label: bool = False) -> str:
    """Plain text of a fragment's HTML (see the module docstring)."""
    if not raw:
        return ""
    s = raw
    if not keep_label:
        s = re.sub(r"^\s*<var>.*?</var>\s*", "", s, count=1, flags=re.S)
    s = re.sub(r"<sup>(.*?)</sup>", lambda m: re.sub(r"<[^>]+>", "", m.group(1)).translate(SUPER), s, flags=re.S)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"</t[dh]>\s*(?=<t[dh])", " | ", s, flags=re.I)
    s = re.sub(r"</tr>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = html.unescape(s)
    lines = [re.sub(r"[ \t\r\xa0]+", " ", line).strip() for line in s.split("\n")]
    return "\n".join(line for line in lines if line)


def _date(value: Any) -> date | None:
    return date.fromisoformat(value) if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) else None


def _segment(eli: str) -> str:
    return eli.rstrip("/").rsplit("/", 1)[-1]


class _Row:
    def __init__(self, values: dict[str, Any]):
        self.__dict__.update(values)
        self.eli: str
        self.parent: str | None
        self.type: str | None
        self.text: str | None
        self.label_number: str | None
        self.hierarchy: str | None
        self.differs_from: str | None
        self.children: list[_Row] = []
        self.start: date | None = None


def parse_esbirka(content: bytes) -> list[ParsedDocument]:
    try:
        doc = json.loads(content)
    except ValueError as exc:
        raise EsbirkaParseError(f"not JSON: {exc}") from exc
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise EsbirkaParseError(f"unsupported document format {doc.get('format') if isinstance(doc, dict) else None!r}")
    version, meta = doc["version"], doc.get("metadata") or {}
    match = re.fullmatch(r"esel-esb:eli/cz/([a-z0-9]+)/(\d{4})/(\d+)/(\d{4}-\d{2}-\d{2}|0000-00-00)", version["iri"])
    if not match:
        raise EsbirkaParseError(f"unexpected version IRI {version.get('iri')!r}")
    collection, year, number, eli_date = match.group(1), int(match.group(2)), match.group(3), match.group(4)
    act_id = f"{collection}/{year}/{number}"
    start, end = _date(version.get("znění-datum-účinnosti-od")), _date(version.get("znění-datum-účinnosti-do"))
    as_of = _date(doc.get("as_of"))
    if start is None or as_of is None:
        raise EsbirkaParseError(f"{act_id}: a version needs its effective-from date and the as_of date")
    if end is not None and end < start:
        raise EsbirkaParseError(f"{act_id}: the version ends before it starts")
    valid_to = end + timedelta(days=1) if end is not None and end < as_of else None

    rows: list[_Row] = [_Row(dict(zip(doc["columns"], values, strict=True))) for values in doc["fragments"]]
    # document order: the fragment's position among its siblings at every level ("/1/2/"), not the dump's order
    rows.sort(key=lambda r: tuple(int(x) for x in (r.hierarchy or "").split("/") if x.isdigit()))
    by_eli: dict[str, _Row] = {(row.eli or f"/anon/{i}"): row for i, row in enumerate(rows)}
    if not rows:
        raise EsbirkaParseError(f"{act_id}: the version has no fragments")
    roots: list[_Row] = []
    for row in rows:
        parent = by_eli.get(row.parent) if row.parent else None
        (parent.children if parent is not None else roots).append(row)

    # footnotes
    footnotes: dict[str, str] = {}
    for row in rows:
        if row.type == "PPC" and row.label_number:
            footnotes.setdefault(str(row.label_number), f"{row.label_number}) {_clean(row.text)}")

    provisions: list[ParsedProvision] = []
    used: dict[str, int] = {}
    kind_of_instrument = SUBTYPES.get(meta.get("cis-esb-podtyp-právní-akt-položka") or "", "notice")
    if act_id in CONSTITUTIONAL:
        kind_of_instrument = "constitution"

    def effective(row: _Row, inherited: date) -> date:
        own = _date(row.differs_from)
        return max(inherited, own) if own else inherited

    def is_provision(row: _Row) -> bool:
        return (row.type or "") in NUMBERED and row.label_number not in (None, "")

    def text_of(row: _Row, acc: list[str], marks: list[str]) -> None:
        """Text of ``row``'s descendants that are not provisions (or headings, containers) of their own."""
        for child in row.children:
            if is_provision(child) or child.type in CONTAINERS or child.type in SKIPPED:
                continue
            if child.type in ("Nadpis_pod", "Nadpis_nad"):
                continue
            piece = _clean(child.text, keep_label=True)
            if piece:
                acc.append(piece)
                marks += FOOTNOTE_MARK.findall(child.text or "")
            text_of(child, acc, marks)

    def numbered_below(row: _Row) -> list[_Row]:
        out: list[_Row] = []
        for child in row.children:
            if is_provision(child):
                out.append(child)
            elif child.type not in CONTAINERS and child.type not in SKIPPED:
                out += numbered_below(child)
        return out

    def heading_of(row: _Row) -> str | None:
        for child in row.children:
            if child.type == "Nadpis_pod" and _clean(child.text):
                return _clean(child.text)
        return None

    def add(
        eid: str,
        kind: str,
        path: list[str],
        top: str,
        heading: str | None,
        text: str,
        parent: str | None,
        *,
        status: str,
        from_date: date,
        note: str | None,
        context: str | None,
    ) -> int:
        provisions.append(
            ParsedProvision(
                eid=eid,
                kind=kind,
                num_path=path,
                top_kind=top,
                heading=heading,
                text=text,
                parent_eid=parent,
                sort_order=len(provisions),
                status=status,  # type: ignore[arg-type]
                valid_from=from_date,
                valid_to=valid_to,
                extent=[JURISDICTION],
                note=note,
                refs=[],
                is_leaf=True,
                has_own_content=bool(text),
                context_heading=context,
            )
        )
        return len(provisions) - 1

    def unique(eid: str) -> str:
        used[eid] = used.get(eid, 0) + 1
        return eid if used[eid] == 1 else f"{eid}-{used[eid]}"

    def emit(row: _Row, parent: str | None, path: list[str], context: str | None, inherited: date) -> None:
        from_date = effective(row, inherited)
        if valid_to is not None and from_date >= valid_to:
            return  # does not apply while this version does
        kind, path_kind = NUMBERED[row.type or ""]
        eid = unique(f"{parent}.{_segment(row.eli)}" if parent else _segment(row.eli))
        own = _clean(row.text)
        marks = FOOTNOTE_MARK.findall(row.text or "")
        parts = [own] if own else []
        text_of(row, parts, marks)
        text = "\n".join(parts)
        repealed = bool(REPEALED.match(text))
        title = heading_of(row)
        here = [*path, f"{path_kind}:{row.label_number}"]
        notes: list[str] = []
        for mark in marks:
            if mark in footnotes and footnotes[mark] not in notes:
                notes.append(footnotes[mark])
        index = add(
            eid,
            kind,
            here,
            "section",
            title,
            "" if repealed else text,
            parent,
            status="repealed" if repealed else ("prospective" if from_date > as_of else "in_force"),
            from_date=from_date,
            note="\n".join(notes) or ("Zrušeno" if repealed else None),
            context=title or context,
        )
        provisions[index].has_own_content = bool(text) and not repealed
        for child in numbered_below(row):
            provisions[index].is_leaf = False
            emit(child, eid, here, title or context, from_date)

    def preamble(row: _Row) -> None:
        lines = [_clean(r.text, keep_label=True) for r in row.children if r.type == "Preambule"]
        body = "\n".join(line for line in lines if line)
        if body and not any(p.eid == "preamble" for p in provisions):
            add(
                "preamble",
                "preamble",
                ["preamble:preambule"],
                "preamble",
                "Preambule",
                body,
                None,
                status="in_force",
                from_date=start,
                note=None,
                context="Preambule",
            )

    def annex(row: _Row) -> None:
        parts: list[str] = []

        def gather(r: _Row) -> None:
            piece = _clean(r.text, keep_label=True)
            if piece:
                parts.append(piece)
            for c in r.children:
                gather(c)

        gather(row)
        head = _clean(row.text, keep_label=True) or None
        seg = _segment(row.eli)
        add(
            unique(seg),
            "annex",
            [f"annex:{seg.removeprefix('priloha_')}"],
            "annex",
            head,
            "\n".join(parts),
            None,
            status="in_force",
            from_date=start,
            note=None,
            context=head,
        )

    def walk(row: _Row, context: str | None, group: str | None, inherited: date) -> None:
        pending = group
        for child in row.children:
            t = child.type or ""
            if t == "Virtual_Prefix":
                preamble(child)
            elif t in SKIPPED:
                continue
            elif t == "Hlavicka_priloha":
                annex(child)
            elif is_provision(child):
                emit(child, None, [], pending or context, effective(row, inherited))
            elif t in CONTAINERS:
                title = " ".join(x for x in (_clean(child.text, keep_label=True), heading_of(child)) if x) or None
                walk(child, title or context, None, effective(child, inherited))
            elif t == "Nadpis_nad":
                pending = _clean(child.text) or pending
            elif t in ("Nadpis_pod", "Nadpis"):
                continue
            elif child.children:
                walk(child, context, pending, effective(child, inherited))

    for top in roots:
        if top.type == "Virtual_Document":
            walk(top, None, None, start)
    if not provisions:
        raise ParseSkipped(f"{act_id}: no provision of the version {eli_date} applies while it does")

    title = _clean(meta.get("metadata-název")) or _clean(version.get("akt-název-vyhlášený"))
    work_title = _clean(version.get("akt-název-vyhlášený")) or title
    if not title:
        raise EsbirkaParseError(f"{act_id}: no title")
    citation = f"{number}/{year} Sb."
    ext = {"year": year, "number": number, "collection": collection, "kind": meta.get("cis-esb-druh-aktu-položka")}
    events = [
        ParsedEvent(
            "version_change",
            start,
            None,
            f"Version ({'as promulgated' if eli_date == '0000-00-00' else 'time version'}) in force from {start}",
            "znění",
        )
    ]
    work_uri = f"/cz/{collection}/{year}/{int(number)}"
    return [
        ParsedDocument(
            doc_kind="legislation",
            instrument_type=kind_of_instrument,
            jurisdiction_id=JURISDICTION,
            work_uri=work_uri,
            expression_uri=f"{work_uri}/ces@{eli_date}",
            work_title=work_title,
            title=title,
            short_title=_clean(meta.get("metadata-název-zkrácený")) or None,
            number=citation,
            work_date=_date(meta.get("metadata-datum-schválení")),
            language="cs",
            authoritative=False,
            text_origin="official_consolidation",
            consolidation_kind="point_in_time",
            version_date=start,
            currency_date=None,
            extent=[JURISDICTION],
            ext_module="cz",
            ext={k: v for k, v in ext.items() if v is not None},
            events=events,
            provisions=provisions,
            validity_basis="revision_snapshot",
        )
    ]


def resolve_uri(uri: str) -> tuple[str, str | None] | None:
    """``/cz/sb/<year>/<number>[/~<eid>]`` -> (work URI, eId); other URIs are not Czech."""
    m = re.match(r"^(/cz/sb/\d{4}/\d+)(?:/~(.+))?$", uri.strip())
    return (m.group(1), m.group(2)) if m else None
