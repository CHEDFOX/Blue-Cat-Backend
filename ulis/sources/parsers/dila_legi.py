"""Parser for the DILA "LEGI" consolidated legislation bundles built by ``connectors/dila_legi.py``.

A bundle is a zip holding, byte for byte, the files the DILA publishes for one consolidated text:

* ``version/<id>.xml`` (``TEXTE_VERSION``): title, nature, number, dates, state of the text;
* ``struct/<id>.xml`` (``TEXTELR``): the top of the structure (sections and articles listed with their periods);
* ``section_ta/<id>.xml`` (``SECTION_TA``): one version of a section (title, children);
* ``article/<id>.xml`` (``ARTICLE``): ONE VERSION of an article: ``NUM``, ``ETAT``, ``DATE_DEBUT``, ``DATE_FIN``, the
  section titles above it (``CONTEXTE``), the other versions of the same article (``VERSIONS``), the text
  (``BLOC_TEXTUEL``), a note (``NOTA``) and links to other texts (``LIENS``);
* ``bundle.json``: provenance added by the connector (archives applied, currency date).

Mapping to ULIS (``validity_basis = source_periods``; the publisher states the period of every version):

* one work per consolidated text, ``/fr/legi/<CID>`` (``LEGITEXT...`` for a code, ``JORFTEXT...`` for a law,
  ordinance...); ONE expression holding the whole history (``consolidation_kind = version_history``), dated at the
  last archive applied (the currency date);
* one provision per article *number* (``art-<number>``): the versions of an article are provision versions with the
  source's ``DATE_DEBUT`` / ``DATE_FIN``. Two article chains that carry the same number at different times (a number
  re-used after a reform) are one provision; if they overlap in time the later chain gets a suffix;
* sections (``Livre``, ``Titre``, ``Chapitre``...) are container provisions (``sec-<cid>``) with their title versions;
  an article version's heading is the title of the innermost section above it in that version;
* ``2999-01-01`` is the source's "no end": an open end. A year of 2200 or more (``2222-01-01``) is its placeholder for
  an undetermined date: the date is left empty (a version without a start is ``prospective``) and the placeholder is
  quoted in the note;
* ``MODIFIE_MORT_NE`` (modified before it came into force: an empty period) and ``ANNULE`` versions (annulled
  retroactively; the source extends the earlier version over their period) are not ingested;
* when a version ends and no other version of the article starts then, a ``repealed`` version (empty text, the source's
  state and end date in the note) starts at that date;
* the text is the exact text of ``BLOC_TEXTUEL`` (paragraph breaks kept, whitespace runs collapsed); ``NOTA`` and the
  source's links that say how a version came about (``CREATION``, ``MODIFICATION``, ``ABROGATION``...) go to ``note``.

Not ingested: visas, signatories, preparatory works, the link graph (citations, concordances) between legislative
texts, territorial applicability, ``VERSIONS_A_VENIR``.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from xml.etree.ElementTree import Element  # noqa: S405 - type only; parsing uses defusedxml

from defusedxml.ElementTree import fromstring

from ulis.sources.normalized import ParsedDocument, ParsedEvent, ParsedProvision, ParseSkipped

PARSER_ID = "dila_legi_xml"
PARSER_VERSION = "1.0.0"
SITE = "https://www.legifrance.gouv.fr"
OPEN_END = "2999"  # DATE_FIN 2999-01-01: no end
PLACEHOLDER_FROM_YEAR = 2200  # 2222-01-01 / 2222-02-22: "date not fixed"
SKIPPED_STATES = {"MODIFIE_MORT_NE", "ANNULE"}
AMENDMENT_LINKS = {"CREATION", "MODIFICATION", "ABROGATION", "ANNULATION", "CODIFICATION", "TRANSFERT", "DEPLACEMENT"}
MAX_MEMBER = 64 * 1024 * 1024
MAX_TOTAL = 3 * 1024 * 1024 * 1024
MEMBER_NAME = re.compile(r"bundle\.json|(?:struct|version|article|section_ta)/[A-Z]{8}\d{12}\.xml")

# DILA nature -> ULIS instrument type (declared by the FR profile)
INSTRUMENT_TYPES = {
    "CONSTITUTION": "constitution",
    "LOI_CONSTIT": "constitutional_act",
    "LOI_ORGANIQUE": "organic_act",
    "LOI": "act",
    "ORDONNANCE": "ordinance",
    "DECRET_LOI": "decree_law",
    "DECRET": "decree",
    "ARRETE": "order",
    "CODE": "code",
}
OTHER_TYPE = "other_text"
SECTION_KINDS = [
    (re.compile(r"^sous-?\s*sous-?\s*paragraphe", re.I), "subsubparagraph"),
    (re.compile(r"^sous-?\s*paragraphe", re.I), "subparagraph"),
    (re.compile(r"^sous-?\s*section", re.I), "subsection"),
    (re.compile(r"^sous-?\s*titre", re.I), "subtitle"),
    (re.compile(r"^paragraphe", re.I), "paragraph"),
    (re.compile(r"^section", re.I), "section"),
    (re.compile(r"^chapitre", re.I), "chapter"),
    (re.compile(r"^titre", re.I), "title"),
    (re.compile(r"^livre", re.I), "book"),
    (re.compile(r"^partie", re.I), "part"),
    (re.compile(r"^annexe", re.I), "annex"),
]
BLOCK_TAGS = {"p", "div", "li", "tr", "ul", "ol", "table", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6", "pre"}


def work_uri(cid: str) -> str:
    return f"/fr/legi/{cid}"


def article_slug(num: str) -> str:
    """The language-neutral key of an article number: ``L. 233-3`` and ``L233-3`` are one article, ``R*229-8`` is
    ``R229-8`` (the star marks a Conseil d'Etat decree and is not part of the number)."""
    return re.sub(r"[^0-9A-Za-z-]", "", num) or "x"


def article_eid(num: str) -> str:
    return f"art-{article_slug(num)}"


def section_eid(cid: str) -> str:
    return "sec-" + (cid.removeprefix("LEGISCTA").lstrip("0") or "0")


# ---------------------------------------------------------------------------------------------------- helpers
def parse_date(value: str | None) -> tuple[date | None, str]:
    """``(date, kind)`` with kind ``date`` | ``open`` (2999: no end) | ``placeholder`` (2200+) | ``missing``."""
    if not value:
        return None, "missing"
    if value.startswith(OPEN_END):
        return None, "open"
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None, "missing"
    if parsed.year >= PLACEHOLDER_FROM_YEAR:
        return None, "placeholder"
    return parsed, "date"


def _text_of(element: Element | None, path: str) -> str | None:
    if element is None:
        return None
    found = element.find(path)
    return ((found.text or "").strip() or None) if found is not None else None


def render_text(element: Element | None) -> str:
    """Plain text of a ``CONTENU`` element: block tags and ``<br/>`` end a line, table cells are tab separated."""
    if element is None:
        return ""
    out: list[str] = []

    def walk(node: Element) -> None:
        tag = node.tag.lower() if isinstance(node.tag, str) else ""
        if tag == "br" or tag in BLOCK_TAGS:
            out.append("\n")
        elif tag in ("td", "th"):
            out.append("\t")
        out.append(node.text or "")
        for child in node:
            walk(child)
            out.append(child.tail or "")
        if tag in BLOCK_TAGS:
            out.append("\n")

    walk(element)
    lines = []
    for raw in "".join(out).split("\n"):
        cells = [re.sub(r"[ \r\f\v]+", " ", cell).strip() for cell in raw.split("\t")]
        line = "\t".join(c for c in cells if c)
        if line:
            lines.append(line)
    return "\n".join(lines)


def section_kind(title: str) -> tuple[str, list[str]]:
    """Kind and number path of a section from its title (``Chapitre III : Des filiales`` -> chapter, ``["III"]``)."""
    head = title.split(":", 1)[0].strip()
    for pattern, kind in SECTION_KINDS:
        match = pattern.match(head)
        if match:
            label = head[match.end() :].strip(" .-")
            return kind, ([label] if label and len(label.split()) <= 3 else [])
    return "division", []


@dataclass
class _Art:
    """One article version file."""

    id: str
    num: str
    etat: str
    debut: date | None
    debut_kind: str
    fin: date | None
    fin_kind: str
    chain: str
    heading: str | None
    text: str
    note: str | None
    listed: list[tuple[str, str, str, str]] = field(default_factory=list)  # (debut, etat, fin, id) of VERSIONS
    context_section: str | None = None  # id of the innermost section above this version (CONTEXTE)


@dataclass
class _Sec:
    """One section version (from the listing that names it; ``children`` from its own file when present)."""

    id: str
    cid: str
    title: str
    debut: date | None
    debut_kind: str
    fin: date | None
    fin_kind: str
    etat: str
    children: list[tuple[str, str]] = field(default_factory=list)  # ("sec"|"art", version id), in listed order
    has_file: bool = False


def _art(root: Element, file_id: str) -> _Art:
    spec = root.find("META/META_SPEC/META_ARTICLE")
    debut, debut_kind = parse_date(_text_of(spec, "DATE_DEBUT"))
    fin, fin_kind = parse_date(_text_of(spec, "DATE_FIN"))
    listed = [
        (v.get("debut", ""), v.get("etat", ""), v.get("fin", ""), v.get("id", ""))
        for v in root.iterfind("VERSIONS/VERSION/LIEN_ART")
    ]
    chain_ids = {entry[3] for entry in listed if entry[3]} | {file_id}
    section_titles = list(root.iterfind("CONTEXTE/TEXTE//TITRE_TM"))
    titles = [(t.text or "").strip() for t in section_titles]
    context_section = section_titles[-1].get("id") if section_titles else None
    note_parts = []
    nota = render_text(root.find("NOTA/CONTENU"))
    if nota:
        note_parts.append(nota)
    for link in root.iterfind("LIENS/LIEN"):
        label = (link.text or "").strip()
        if link.get("sens") == "source" and link.get("typelien") in AMENDMENT_LINKS and label:
            note_parts.append(f"{link.get('typelien')}: {label}")
    return _Art(
        id=file_id,
        num=_text_of(spec, "NUM") or "",
        etat=_text_of(spec, "ETAT") or "",
        debut=debut,
        debut_kind=debut_kind,
        fin=fin,
        fin_kind=fin_kind,
        chain=min(chain_ids),
        heading=titles[-1] if titles and titles[-1] else None,
        text=render_text(root.find("BLOC_TEXTUEL/CONTENU")),
        note="\n".join(note_parts) or None,
        listed=listed,
        context_section=context_section,
    )


def _children(root: Element, container: str) -> tuple[list[_Sec], list[tuple[str, str]]]:
    """The section versions named under ``container`` and all its children (sections and articles) in order."""
    sections: list[_Sec] = []
    order: list[tuple[str, str]] = []
    holder = root.find(container)
    for entry in holder if holder is not None else []:
        if entry.tag == "LIEN_ART":
            order.append(("art", entry.get("id", "")))
        elif entry.tag == "LIEN_SECTION_TA":
            debut, debut_kind = parse_date(entry.get("debut"))
            fin, fin_kind = parse_date(entry.get("fin"))
            ident = entry.get("id", "")
            sections.append(
                _Sec(
                    id=ident,
                    cid=entry.get("cid") or ident,
                    title=(entry.text or "").strip(),
                    debut=debut,
                    debut_kind=debut_kind,
                    fin=fin,
                    fin_kind=fin_kind,
                    etat=entry.get("etat", ""),
                )
            )
            order.append(("sec", ident))
    return sections, order


def _kept(etat: str, debut: date | None, fin: date | None) -> bool:
    """Whether a version with this state and period is ingested (empty and annulled periods are not)."""
    if etat in SKIPPED_STATES:
        return False
    return not (debut is not None and fin is not None and fin <= debut)


def _overlaps(a: list[tuple[date, date | None]], b: list[tuple[date, date | None]]) -> bool:
    for a_from, a_to in a:
        for b_from, b_to in b:
            if (a_to is None or b_from < a_to) and (b_to is None or a_from < b_to):
                return True
    return False


def _suffix(chain: str) -> str:
    return re.sub(r"^[A-Z]+0*", "", chain) or chain


# ---------------------------------------------------------------------------------------------------- parser
def parse_dila_legi(content: bytes) -> list[ParsedDocument]:
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise ValueError("not a DILA LEGI bundle (not a zip)") from exc
    with zf:
        infos = zf.infolist()
        if sum(i.file_size for i in infos) > MAX_TOTAL:
            raise ValueError("bundle too large")
        for info in infos:
            if info.file_size > MAX_MEMBER or not MEMBER_NAME.fullmatch(info.filename):
                raise ValueError(f"unexpected bundle member {info.filename!r}")
        names = [i.filename for i in infos]
        if "bundle.json" not in names:
            raise ValueError("not a DILA LEGI bundle (no bundle.json)")
        meta = json.loads(zf.read("bundle.json"))
        if meta.get("format") != "ulis-dila-legi-bundle/1":
            raise ValueError("unknown bundle format")
        version_names = [n for n in names if n.startswith("version/")]
        struct_names = [n for n in names if n.startswith("struct/")]
        if len(version_names) != 1 or len(struct_names) != 1:
            raise ValueError("a bundle holds exactly one version file and one struct file")
        version_root = fromstring(zf.read(version_names[0]), forbid_dtd=True)
        struct_root = fromstring(zf.read(struct_names[0]), forbid_dtd=True)
        arts: dict[str, _Art] = {}
        secs: dict[str, _Sec] = {}
        top_sections, top_order = _children(struct_root, "STRUCT")
        for sec in top_sections:
            secs.setdefault(sec.id, sec)
        for name in names:
            if name.startswith("article/"):
                ident = name[len("article/") : -len(".xml")]
                arts[ident] = _art(fromstring(zf.read(name), forbid_dtd=True), ident)
        for name in names:
            if name.startswith("section_ta/"):
                ident = name[len("section_ta/") : -len(".xml")]
                root = fromstring(zf.read(name), forbid_dtd=True)
                kids, order = _children(root, "STRUCTURE_TA")
                for kid in kids:
                    secs.setdefault(kid.id, kid)
                sec = secs.get(ident)
                if sec is None:  # a section no listing names: it has no period, so it cannot be placed
                    continue
                sec.children, sec.has_file = order, True
                title = _text_of(root, "TITRE_TA")
                if title:
                    sec.title = title
    return _assemble(meta, version_root, arts, secs, top_order)


def _assemble(
    meta: dict[str, object],
    version_root: Element,
    arts: dict[str, _Art],
    secs: dict[str, _Sec],
    top_order: list[tuple[str, str]],
) -> list[ParsedDocument]:
    spec = version_root.find("META/META_SPEC")
    chronicle = spec.find("META_TEXTE_CHRONICLE") if spec is not None else None
    cid = _text_of(chronicle, "CID")
    if not cid:
        raise ValueError("the version file has no CID")
    nature = _text_of(version_root, "META/META_COMMUN/NATURE") or ""
    title = _text_of(spec, "META_TEXTE_VERSION/TITREFULL") or _text_of(spec, "META_TEXTE_VERSION/TITRE")
    short_title = _text_of(spec, "META_TEXTE_VERSION/TITRE")
    if not title:
        raise ValueError(f"{cid}: the version file has no title")
    currency = date.fromisoformat(str(meta["currency_date"]))

    # ---- article chains, their eids and the versions that are ingested
    chains: dict[str, list[_Art]] = defaultdict(list)
    for art in arts.values():
        chains[art.chain].append(art)
    periods: dict[str, list[tuple[date, date | None]]] = {}
    for key, versions in chains.items():
        periods[key] = [(v.debut, v.fin) for v in versions if v.debut is not None and _kept(v.etat, v.debut, v.fin)]
    chain_num = {
        key: max(versions, key=lambda v: (v.debut or date.min, v.id)).num or versions[0].num
        for key, versions in chains.items()
    }
    taken: dict[str, list[tuple[str, list[tuple[date, date | None]]]]] = defaultdict(list)
    chain_eid: dict[str, str] = {}
    for key in sorted(chains, key=lambda k: (min((p[0] for p in periods[k]), default=date.max), k)):
        base = article_eid(chain_num[key])
        occupied = taken[base]
        eid = base
        if any(_overlaps(periods[key], other) for _, other in occupied):
            eid = f"{base}-{_suffix(key)}"
        occupied.append((eid, periods[key]))
        chain_eid[key] = eid
    art_chain = {a.id: a.chain for a in arts.values()}

    # ---- the structure: parents, order
    sec_chains: dict[str, list[_Sec]] = defaultdict(list)
    for sec in secs.values():
        sec_chains[sec.cid].append(sec)

    def latest(versions: list[_Sec]) -> _Sec:
        usable = [v for v in versions if _kept(v.etat, v.debut, v.fin)] or versions
        return max(usable, key=lambda v: (v.debut or date.min, v.has_file, v.id))

    order_of: dict[str, int] = {}  # node key ("sec:<cid>" | "art:<chain>") -> position
    parent_of: dict[str, str | None] = {}  # node key -> parent section cid
    counter = 0

    def visit(entries: list[tuple[str, str]], parent: str | None, depth: int = 0) -> None:
        nonlocal counter
        for kind, ident in entries:
            if kind == "art":
                chain = art_chain.get(ident)
                key = f"art:{chain}"
                if chain is None or key in order_of:
                    continue
                order_of[key], parent_of[key] = counter, parent
                counter += 1
            else:
                sec = secs.get(ident)
                if sec is None or depth > 40:
                    continue
                key = f"sec:{sec.cid}"
                if key in order_of:
                    continue
                order_of[key], parent_of[key] = counter, parent
                counter += 1
                chain_versions = sorted(sec_chains[sec.cid], key=lambda v: (v.debut or date.min, v.id), reverse=True)
                best = latest(sec_chains[sec.cid])
                ordered = [best, *[v for v in chain_versions if v is not best]]
                visit([c for v in ordered for c in v.children], sec.cid, depth + 1)

    visit(top_order, None)
    # Anything no listing reaches comes last: sections without a parent, then articles placed under the section
    # their own file names (CONTEXTE), when that section is known.
    for cid_key in sorted(sec_chains):
        node = f"sec:{cid_key}"
        if node not in order_of:
            order_of[node], parent_of[node] = counter, None
            counter += 1
    for key in sorted(chains):
        node = f"art:{key}"
        if node not in order_of:
            newest = max(chains[key], key=lambda v: (v.debut or date.min, v.id))
            context = secs.get(newest.context_section or "")
            order_of[node] = counter
            parent_of[node] = context.cid if context is not None and f"sec:{context.cid}" in order_of else None
            counter += 1

    # ---- provisions
    provisions: list[tuple[int, int, ParsedProvision]] = []
    for cid_key, versions in sec_chains.items():
        node = f"sec:{cid_key}"
        eid = section_eid(cid_key)
        parent_cid = parent_of[node]
        for v in sorted(versions, key=lambda x: (x.debut or date.min, x.id)):
            if not _kept(v.etat, v.debut, v.fin) or not v.title:
                continue
            kind, num_path = section_kind(v.title)
            provisions.append(
                (
                    order_of[node],
                    0,
                    ParsedProvision(
                        eid=eid,
                        kind=kind,
                        num_path=num_path,
                        top_kind=kind,
                        heading=v.title,
                        text="",
                        parent_eid=section_eid(parent_cid) if parent_cid else None,
                        sort_order=order_of[node],
                        status="in_force" if v.debut_kind != "placeholder" else "prospective",
                        valid_from=v.debut,
                        valid_to=v.fin,
                        extent=["FR"],
                        note=None,
                        refs=[],
                        is_leaf=False,
                        has_own_content=False,
                    ),
                )
            )
    by_eid: dict[str, list[str]] = defaultdict(list)
    for key, eid in chain_eid.items():
        by_eid[eid].append(key)
    for eid, keys in by_eid.items():
        # the chain of record (position, parent) is the one whose latest version is the most recent
        record = max(keys, key=lambda k: (max((v.debut or date.min for v in chains[k]), default=date.min), k))
        node = f"art:{record}"
        parent_cid = parent_of[node]
        for item in _article_provisions(eid, [v for k in keys for v in chains[k]], chains, keys, order_of[node]):
            item.parent_eid = section_eid(parent_cid) if parent_cid else None
            provisions.append((order_of[node], 1, item))
    provisions.sort(key=lambda t: (t[0], t[1], t[2].valid_from or date.min, t[2].status))
    parsed_provisions = [p for _, _, p in provisions]
    if not any(p.text for p in parsed_provisions):
        raise ParseSkipped(f"{cid}: no article text in the archives")

    nature_type = INSTRUMENT_TYPES.get(nature, OTHER_TYPE)
    work_date, work_kind = parse_date(_text_of(chronicle, "DATE_TEXTE"))
    published, published_kind = parse_date(_text_of(chronicle, "DATE_PUBLI"))
    number = _text_of(chronicle, "NUM")
    events = []
    if work_date is not None and work_kind == "date":
        events.append(ParsedEvent("enactment", work_date, None, None, "META_TEXTE_CHRONICLE/DATE_TEXTE"))
    if published is not None and published_kind == "date":
        events.append(ParsedEvent("publication", published, None, None, "META_TEXTE_CHRONICLE/DATE_PUBLI"))
    ext = {
        "cid": cid,
        "legitext": _text_of(version_root, "META/META_COMMUN/ID"),
        "nature": nature or None,
        "nor": _text_of(chronicle, "NOR"),
        "etat": _text_of(spec, "META_TEXTE_VERSION/ETAT"),
        "tree": meta.get("tree"),
    }
    return [
        ParsedDocument(
            doc_kind="legislation",
            instrument_type=nature_type,
            jurisdiction_id="FR",
            work_uri=work_uri(cid),
            expression_uri=f"{work_uri(cid)}/fra@{currency.isoformat()}",
            work_title=title,
            title=title,
            short_title=short_title or title,
            number=number,
            work_date=work_date,
            language="fr",
            authoritative=False,
            text_origin="official_consolidation",
            consolidation_kind="version_history",
            version_date=currency,
            currency_date=currency,
            extent=["FR"],
            ext_module="fr",
            ext={k: v for k, v in ext.items() if v},
            events=events,
            provisions=parsed_provisions,
            validity_basis="source_periods",
        )
    ]


def _article_provisions(
    eid: str, versions: list[_Art], chains: dict[str, list[_Art]], keys: list[str], position: int
) -> list[ParsedProvision]:
    """The provision versions of one article number: the ingested versions plus repeal markers."""
    kept = [v for v in versions if _kept(v.etat, v.debut, v.fin)]
    # Two files with the same start (an annulled wording's predecessor restored by the source): keep the one that
    # runs longest (an open end is the longest), then the later identifier.
    by_start: dict[date, _Art] = {}
    undated: dict[str, _Art] = {}  # no usable start: one per status (the latest file), the pipeline keys on it
    for v in sorted(kept, key=lambda x: x.id):
        if v.debut is None:
            undated[v.debut_kind] = v
            continue
        old = by_start.get(v.debut)
        if old is None or (v.fin is None, v.fin or date.min) >= (old.fin is None, old.fin or date.min):
            by_start[v.debut] = v
    chosen = [by_start[d] for d in sorted(by_start)] + list(undated.values())
    num = max(versions, key=lambda v: (v.debut or date.min, v.id)).num
    # periods of every non-skipped version the chains list (also those whose file is not in the dump)
    listed_periods: list[tuple[str, date | None, date | None]] = []
    for key in keys:
        for v in chains[key]:
            for debut_s, etat, fin_s, ident in v.listed:
                debut, _ = parse_date(debut_s)
                fin, fin_kind = parse_date(fin_s)
                if etat not in SKIPPED_STATES and debut is not None and (fin is None or fin > debut):
                    listed_periods.append((ident, debut, fin))
    out: list[ParsedProvision] = []
    marker_dates: set[date] = set()
    for v in chosen:
        status = "prospective" if v.debut_kind == "placeholder" else "in_force"
        notes = []
        if v.debut_kind == "placeholder":
            notes.append(f"DATE_DEBUT is the source's placeholder for an undetermined date (ETAT {v.etat}).")
        if v.fin_kind == "placeholder":
            notes.append(f"DATE_FIN is the source's placeholder for an undetermined date (ETAT {v.etat}).")
        if v.num and article_slug(v.num) != article_slug(num):
            notes.append(f"NUM {v.num}")
        if v.note:
            notes.append(v.note)
        out.append(
            ParsedProvision(
                eid=eid,
                kind="article",
                num_path=[num],
                top_kind="article",
                heading=v.heading,
                text=v.text,
                parent_eid=None,
                sort_order=position,
                status=status,  # type: ignore[arg-type]
                valid_from=v.debut,
                valid_to=v.fin,
                extent=["FR"],
                note="\n".join(notes) or None,
                refs=[],
                is_leaf=True,
                has_own_content=True,
            )
        )
        if v.fin is not None and status == "in_force":
            covered = any(
                ident != v.id and start <= v.fin and (end is None or end > v.fin) for ident, start, end in listed_periods
            )
            if not covered and v.fin not in marker_dates:
                marker_dates.add(v.fin)
                out.append(
                    ParsedProvision(
                        eid=eid,
                        kind="article",
                        num_path=[num],
                        top_kind="article",
                        heading=None,
                        text="",
                        parent_eid=None,
                        sort_order=position,
                        status="repealed",
                        valid_from=v.fin,
                        valid_to=None,
                        extent=["FR"],
                        note=f"ETAT {v.etat}; DATE_FIN {v.fin.isoformat()}.",
                        refs=[],
                        is_leaf=True,
                        has_own_content=False,
                    )
                )
    return out
