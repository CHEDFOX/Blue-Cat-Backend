"""Direct citation lookup: "§ 823 Abs. 1 BGB", "Art. 6(1)(f) GDPR", "3 kap. 1 § brottsbalken", "刑法第百九十九条".

Lawyers type the citation, not the words of the provision, and full-text search cannot find it: the numbers of a
provision are not part of its indexed text. A citation is parsed into the cited unit (``823``, ``1``) and the rest of
the query, which names the law (``BGB``). The law is found by a curated short name (``config/citation-aliases.yaml``),
by the publisher's abbreviation, number or title in any of its languages, or, for the US CFR and the US Code, from the
citation itself. The unit is then found by the provision's stored number path, compared after normalisation
("§ 823" / "(1)", "art_6" / "para_1" / "point_f", "ch:3" / "s:1"). A chapter the citation leaves out ("36 §
avtalslagen" for chapter 3, § 36) is skipped; a subdivision that does not exist falls back to the nearest enclosing
unit, and the response says so.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import text
from sqlalchemy.orm import Session

from ulis.settings import REPO_ROOT

ALIASES_FILE = REPO_ROOT / "config" / "citation-aliases.yaml"

# Kept identical to the expression indexes of migration 0007, so that PostgreSQL can use them.
INSTRUMENT_NAMES = (
    "to_tsvector('simple', coalesce(i.short_title, '') || ' ' || coalesce(i.number, '') || ' ' || "
    "coalesce(i.ext ->> 'jurabk', '') || ' ' || coalesce(i.ext ->> 'amtabk', '') || ' ' || "
    "coalesce(i.ext ->> 'abbrev', ''))"
)
VERSION_NAMES = "to_tsvector('simple', coalesce(iv.short_title, '') || ' ' || left(iv.title, 300))"

_CHAPTER_KINDS = {"ch", "chp", "chapter", "kap", "luku"}
# Path elements that are never the target of a numbered citation of the body of a law.
_SKIPPED_KINDS = {"def", "bil", "tr", "body", "anx", "rct", "anhang", "anlage"}

# --------------------------------------------------------------------------- parsing

_JP_NUM = "[0-9０-９〇一二三四五六七八九十百千]+"
_NUM = (
    r"(?:[LRDA](?=\d))?\d+[A-Za-z]{0,3}(?:[.\-]\d+[A-Za-z]{0,3})*"
    r"(?:\s(?:bis|ter|quater|quinquies|sexies|septies|octies|[a-z])(?![\w.]))?"
)
_PARENS = r"(?:\s*\(\s*[0-9]+[a-z]?(?:\.\d+)?\s*\)|\s*\(\s*[a-z]{1,4}\s*\))*"

_US_CFR = re.compile(
    r"\b(\d{1,2})\s*C\.?\s?F\.?\s?R\.?\s*(?:§+|part|pt\.?|sec(?:tion)?\.?)?\s*"
    r"(\d+)(?:\.(\d+[A-Za-z]?(?:\([a-z0-9]+\)(?=-\d))?(?:-\d+[A-Za-z]?)*))?(" + _PARENS + ")",
    re.IGNORECASE,
)
_US_CODE = re.compile(
    r"\b(\d{1,2})\s*U\.?\s?S\.?\s?C\.?(?:\s?A\.?)?\s*(?:§+\s*)?(\d+[a-z]*(?:-\d+[a-z]?)?)(" + _PARENS + ")",
    re.IGNORECASE,
)
_JAPANESE = re.compile(rf"第?({_JP_NUM})条((?:の{_JP_NUM})*)(?:第?({_JP_NUM})項)?(?:第?({_JP_NUM})号)?")
# "RL 28:1", "BrB 3:1": chapter:section. A four-digit first number is a Swedish statute number (1962:700) instead.
_CHAPTER_SECTION = re.compile(r"(?<![\d:/])(\d{1,3}[a-z]?)\s?:\s?(\d+[a-z]?)(?![\d:/])")
_CHAPTER = re.compile(
    rf"(?:\b({_NUM})\s*(?:kap\.?|kapitlet|kapitel|luku|luvun|luvussa)(?!\w))"
    rf"|(?:\b(?:kap(?:itel)?\.?|chapter|ch\.|luku)\s*({_NUM}))",
    re.IGNORECASE,
)
_MAIN_BEFORE = re.compile(
    r"(?:§§?|\bart(?:icle|ikel|ículo|iculo|igo)?s?\.?|\bsec(?:tion|t)?s?\.?|\bss?\.|\bs(?=\s?\d)"
    r"|\breg(?:ulation)?s?\.?(?=\s?\d)|\brules?(?=\s\d)|\br\.(?=\s?\d)|\badran\b|\ba\.(?=\s?\d))"
    rf"\s*({_NUM})({_PARENS})",
    re.IGNORECASE,
)
# Swedish and Finnish put the number before the section mark ("2 § 3 st."): a number after it is then not the section.
_MAIN_AFTER = re.compile(rf"\b({_NUM})\s*§")
_SUB_BEFORE = re.compile(
    r"\b(?:Abs(?:atz)?\.?|UAbs\.?|para(?:graph)?\.?|paragraphe|apartado|subs(?:ection)?\.?|is-adran|"
    r"Nr\.?|Nummer|item|point|punkt|lit\.?|Buchst(?:abe)?\.?|letra|letter)\s*\(?([0-9]+[a-z]?|[a-z]{1,3})\)?(?!\w)",
    re.IGNORECASE,
)
_SUB_AFTER = re.compile(
    r"\b(\d+[a-z]?)\s*(?:st\.|stycket|mom\.|momentti|momentin|kohta|kohdan|p\.|punkten)(?!\w)", re.IGNORECASE
)
_SENTENCE = re.compile(r"\b(?:S\.|Satz|[Ss]entence|meningen)\s*\d+(?!\w)")
_FR_CODE_ARTICLE = re.compile(r"\b([LRDA])\.\s?(?=\d)")
_CONNECTORS = {
    "of", "the", "du", "de", "la", "le", "des", "del", "der", "die", "das", "dem", "den", "zum", "zur", "y", "e",
    "i", "in", "av", "och", "and", "et", "und", "under", "ff", "f", "ivm", "iVm", "vgl", "cf", "see",
}  # fmt: skip


@dataclass(frozen=True)
class Unit:
    kind: str  # "chapter" or ""
    value: str  # normalised: "823", "1", "f", "825-305", "9-2"


@dataclass
class ParsedCitation:
    units: list[Unit]
    main: int  # index of the main unit (section, article, §) in ``units``
    cited: str  # the citation as written
    reference: str  # the rest of the query, which names the law
    uri: str | None = None  # the law itself, when the citation names it (US CFR part, US Code title)
    jurisdiction: str | None = None  # for ``uri``


def normalise(raw: str) -> tuple[str, str]:
    """(kind, value) of one element of a number path or of a cited unit: "§ 823" -> ("", "823"), "ch:3" ->
    ("ch", "3"), "art_6" -> ("art", "6"), "(ii.1)" -> ("", "ii-1"), "s:49 f" -> ("s", "49f"), "1.º" -> ("", "1")."""
    x = unicodedata.normalize("NFKC", re.sub("[ºª°]", "", raw)).strip().lower()  # NFKC would turn "º" into "o"
    kind = ""
    m = re.match(r"^([a-z]+)[_:]", x)
    if m:
        kind, x = m.group(1), x[m.end() :]
    m = re.match(r"^(art|artikel|anhang|anlage)\b\.?\s*", x)
    if m:
        kind, x = m.group(1), x[m.end() :]
    x = re.sub(r"[§()\[\]^ºª°\s]", "", x)
    return kind, re.sub(r"[._:\-]+", "-", x).strip("-")


def _jp_number(s: str) -> int:
    s = unicodedata.normalize("NFKC", s)
    if s.isdigit():
        return int(s)
    digits = {c: i for i, c in enumerate("〇一二三四五六七八九")}
    total, current = 0, 0
    for ch in s:
        if ch in digits:
            current = current * 10 + digits[ch]
        elif ch.isdigit():
            current = current * 10 + int(ch)
        else:
            total += (current or 1) * {"十": 10, "百": 100, "千": 1000}[ch]
            current = 0
    return total + current


def _parens(group: str | None) -> list[str]:
    return re.findall(r"\(\s*([^()\s]+)\s*\)", group or "")


def _unit(value: str, kind: str = "") -> Unit:
    return Unit(kind, normalise(value)[1])


def parse_citation(q: str) -> ParsedCitation | None:
    """The cited unit and the remaining words of a query, or None when the query cites no numbered unit."""
    q = _FR_CODE_ARTICLE.sub(r"\1", unicodedata.normalize("NFKC", q))
    spans: list[tuple[int, int]] = []
    units: list[Unit] = []
    main = 0
    uri = jurisdiction = None

    if m := _US_CFR.search(q):
        title, part, rest, subs = m.groups()
        uri, jurisdiction = f"/us/cfr/t{int(title)}/p{part}", "US"
        if rest is None:
            return None  # a whole part: not a provision
        units = [_unit(f"{part}.{rest}"), *(_unit(s) for s in _parens(subs))]
        spans.append(m.span())
    elif m := _US_CODE.search(q):
        title, section, subs = m.groups()
        uri, jurisdiction = f"/us/usc/t{int(title)}", "US"
        units = [_unit(section), *(_unit(s) for s in _parens(subs))]
        spans.append(m.span())
    elif m := _JAPANESE.search(q):
        article, branches, paragraph, item = m.groups()
        value = "-".join(str(_jp_number(n)) for n in [article, *re.findall(_JP_NUM, branches or "")])
        units = [Unit("", value)]
        units += [Unit("", str(_jp_number(n))) for n in (paragraph, item) if n]
        spans.append(m.span())
    else:
        chapter = _CHAPTER.search(q)
        pair = _CHAPTER_SECTION.search(q)
        found = _MAIN_AFTER.search(q) or _MAIN_BEFORE.search(q)
        if pair and not found:
            units, main = [_unit(pair.group(1), "chapter"), _unit(pair.group(2))], 1
            spans.append(pair.span())
            after = pair.end()
        elif found:
            if chapter and chapter.start() < found.start():
                units.append(_unit(chapter.group(1) or chapter.group(2), "chapter"))
                spans.append(chapter.span())
                main = 1
            units.append(_unit(found.group(1)))
            if found.re is _MAIN_BEFORE:
                units += [_unit(s) for s in _parens(found.group(2))]
            spans.append(found.span())
            after = found.end()
        else:
            return None
        subs = sorted(
            [(m.start(), m.group(1), m.span()) for m in _SUB_BEFORE.finditer(q, after)]
            + [(m.start(), m.group(1), m.span()) for m in _SUB_AFTER.finditer(q, after)]
        )
        for _, value, span in subs:
            units.append(_unit(value))
            spans.append(span)
        spans += [m.span() for m in _SENTENCE.finditer(q, after)]
    if not units or not units[main].value:
        return None
    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    rest = q
    for start, end in reversed(merged):
        rest = rest[:start] + " " + rest[end:]
    cited = " ".join(q[s:e].strip() for s, e in merged)
    return ParsedCitation(units, main, cited, " ".join(re.split(r"[\s,;]+", rest)).strip(" .,;"), uri, jurisdiction)


# --------------------------------------------------------------------------- resolving the law


def name_key(s: str) -> str:
    """Case, accents kept; punctuation and spaces dropped: "C. consom." -> "cconsom", "DS-GVO" -> "dsgvo"."""
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", s).lower())


def _strip_qualifiers(title: str) -> str:
    """'Brottsbalk (1962:700)' -> 'Brottsbalk'; '... Act 2016 (as at 2026-06-01)' -> '... Act 2016'."""
    while True:
        stripped = re.sub(r"\s*\([^()]*\)\s*$", "", title).strip()
        if stripped == title or not stripped:
            return title
        title = stripped


def _variants(key: str) -> list[str]:
    """Inflected forms lawyers write: Swedish definite ("brottsbalken" -> "brottsbalk"), Finnish genitive
    ("rikoslain" -> "rikoslaki", "maakaaren" -> "maakaari")."""
    out = [key]
    if len(key) >= 6:
        for suffix, base in (("lain", "laki"), ("kaaren", "kaari"), ("asetuksen", "asetus"), ("en", ""), ("et", "")):
            if key.endswith(suffix):
                out.append(key[: -len(suffix)] + base)
    return out


@dataclass
class _Alias:
    jurisdiction: str
    spec: dict[str, Any]


@dataclass
class _Aliases:
    mtime: float = -1.0
    by_name: dict[str, list[_Alias]] = field(default_factory=dict)


_ALIASES = _Aliases()


def _aliases(path: Path = ALIASES_FILE) -> dict[str, list[_Alias]]:
    mtime = path.stat().st_mtime if path.exists() else 0.0
    if mtime != _ALIASES.mtime:
        by_name: dict[str, list[_Alias]] = {}
        data = yaml.safe_load(path.read_text("utf-8")) if path.exists() else {}
        for jurisdiction, entries in (data or {}).items():
            for entry in entries:
                spec = {k: v for k, v in entry.items() if k != "names"}
                for name in entry["names"]:
                    by_name.setdefault(name_key(str(name)), []).append(_Alias(str(jurisdiction), spec))
        _ALIASES.by_name, _ALIASES.mtime = by_name, mtime
    return _ALIASES.by_name


def _alias_instruments(session: Session, alias: _Alias) -> list[str]:
    spec, params = alias.spec, {"j": alias.jurisdiction}
    if "uri" in spec:
        sql, params = "SELECT id FROM instruments WHERE uri = :v", {"v": spec["uri"]}
    elif "number" in spec:
        sql, params["v"] = "SELECT id FROM instruments WHERE jurisdiction_id = :j AND number = :v", str(spec["number"])
    elif "title" in spec:
        sql = "SELECT id FROM instruments WHERE jurisdiction_id = :j AND (title = :v OR short_title = :v)"
        params["v"] = spec["title"]
    else:
        ((params["k"], params["v"]),) = spec["ext"].items()
        sql = "SELECT id FROM instruments WHERE jurisdiction_id = :j AND ext ->> :k = :v"
    return [str(i) for i in session.scalars(text(sql), params)]


@dataclass
class _Match:
    instrument_id: str
    jurisdiction: str
    score: int  # 4 curated name, 3 exact name, 2 name starting with the words
    words: int  # how many words of the query name the law


def _span_matches(session: Session, span: str, key: str, scope: list[str] | None) -> list[_Match]:
    words = len(span.split())
    out: list[_Match] = []
    where = " AND i.jurisdiction_id = ANY(CAST(:scope AS text[]))" if scope is not None else ""
    keys = _variants(key)
    terms = " ".join(re.findall(r"\w+", span)) or span
    for kind, sql in (
        (
            "instrument",
            "SELECT CAST(i.id AS text), i.jurisdiction_id, i.short_title, i.number, i.ext ->> 'jurabk', "
            f"i.ext ->> 'amtabk', i.ext ->> 'abbrev' FROM instruments i WHERE {INSTRUMENT_NAMES} "
            f"@@ plainto_tsquery('simple', :terms){where} LIMIT 200",
        ),
        (
            "version",
            "SELECT DISTINCT CAST(iv.instrument_id AS text), i.jurisdiction_id, iv.short_title, iv.title "
            f"FROM instrument_versions iv JOIN instruments i ON i.id = iv.instrument_id WHERE {VERSION_NAMES} "
            f"@@ plainto_tsquery('simple', :terms){where} LIMIT 200",
        ),
    ):
        for instrument_id, jurisdiction, *names in session.execute(text(sql), {"terms": terms, "scope": scope}):
            best = 0
            for i, name in enumerate(names):
                if not name:
                    continue
                is_number = kind == "instrument" and i == 1  # short_title, number, jurabk, amtabk, abbrev
                name_keys = {name_key(name), name_key(_strip_qualifiers(name))}
                if any(k in name_keys for k in keys) and (not is_number or len(key) >= 4):
                    best = 3
                elif not is_number and any(
                    nk.startswith(k) and nk[len(k) :].isdigit() for nk in name_keys for k in keys if len(k) >= 4
                ):
                    best = max(best, 2)  # "Corporations Act" for "Corporations Act 2001"
            if best:
                out.append(_Match(instrument_id, jurisdiction, best, words))
    return out


# At most this many name lookups per citation (each one an index search per table).
MAX_NAME_LOOKUPS = 16


def find_instruments(session: Session, reference: str, scope: list[str] | None, target: str | None) -> list[str]:
    """The instruments a reference names, best first: at most three equally good ones.

    Every run of consecutive words is tried, longest first, skipping runs that begin or end with a connector ("du
    Code civil" -> "Code civil"): first as a curated short name, then against the laws' abbreviations, numbers and
    titles.
    """
    tokens = reference.split()
    spans = sorted(
        (
            " ".join(tokens[i:j])
            for i in range(len(tokens))
            for j in range(i + 1, min(len(tokens), i + 8) + 1)
            if tokens[i] not in _CONNECTORS and tokens[j - 1].strip(".") not in _CONNECTORS  # "du", not "ET"
        ),
        key=lambda s: -len(s.split()),
    )
    spans = [s for s in spans if len(name_key(s)) >= 2]
    matches: list[_Match] = []
    aliases = _aliases()
    for span in spans:
        key = name_key(span)
        for alias in aliases.get(key, []):
            # A short abbreviation means different laws in different places: only within a named jurisdiction.
            if (scope is None and len(key) > 3) or (scope is not None and alias.jurisdiction in scope):
                ids = _alias_instruments(session, alias)
                matches += [_Match(i, alias.jurisdiction, 4, len(span.split())) for i in ids]
    if not matches:
        for span in spans[:MAX_NAME_LOOKUPS]:
            matches += _span_matches(session, span, name_key(span), scope)
            if any(m.score == 3 for m in matches):
                break  # the longest run that is a law's exact name
    rank: dict[str, tuple[int, int, bool]] = {}
    for m in matches:
        rank[m.instrument_id] = max(
            rank.get(m.instrument_id, (0, 0, False)), (m.score, m.words, m.jurisdiction == target)
        )
    if not rank:
        return []
    top = max(rank.values())
    return [i for i, r in sorted(rank.items()) if r == top][:3]


# --------------------------------------------------------------------------- resolving the unit


def _path(num_path: list[str]) -> list[tuple[str, str]] | None:
    path = [normalise(x) for x in num_path if x]
    if not path or any(kind in _SKIPPED_KINDS for kind, _ in path):
        return None
    return path


def _depth(path: list[tuple[str, str]], units: list[Unit]) -> int:
    """How many cited units, outermost first, the provision's path starts with."""
    if units and units[0].kind == "chapter":
        if path[0][0] in _CHAPTER_KINDS:
            if path[0][1] != units[0].value:
                return 0
            path, units, offset = path[1:], units[1:], 1
        else:
            units, offset = units[1:], 1  # the law has no chapters: the cited chapter is left aside
    else:
        offset = 0
        while path and path[0][0] in _CHAPTER_KINDS:
            path = path[1:]
    n = 0
    for (_, value), unit in zip(path, units, strict=False):
        if value != unit.value:
            break
        n += 1
    return n + offset if n else 0


@dataclass
class ResolvedCitation:
    cited: str
    instrument_ids: list[str]
    provision_ids: list[str]  # index units inside the cited unit, in document order
    exact: bool  # False: the cited subdivision does not exist; the enclosing unit is returned


def resolve(
    session: Session, parsed: ParsedCitation, scope: list[str] | None, target: str | None
) -> ResolvedCitation | None:
    if parsed.uri is not None:
        if scope is not None and parsed.jurisdiction not in scope:
            return None
        instrument_ids = [
            str(i) for i in session.scalars(text("SELECT id FROM instruments WHERE uri = :u"), {"u": parsed.uri})
        ]
    else:
        instrument_ids = find_instruments(session, parsed.reference, scope, target)
    if not instrument_ids:
        return None
    rows = [
        (str(pid), path, index_unit, order)
        for pid, num_path, index_unit, order in session.execute(
            text(
                "SELECT id, num_path, index_unit, sort_order FROM provisions "
                "WHERE instrument_id = ANY(CAST(:ids AS uuid[])) ORDER BY instrument_id, sort_order"
            ),
            {"ids": instrument_ids},
        )
        if (path := _path(list(num_path))) is not None
    ]
    alternatives = [parsed.units]
    main = parsed.units[parsed.main]
    if "-" in main.value:  # "art. 56.1 ET", "art. 18.1 CE": article 56, paragraph 1; "artikel 6.1 e": 6(1)(e)
        parts = main.value.split("-")
        last = re.fullmatch(r"(\d+)([a-z])", parts[-1])
        lettered = [*parts[:-1], *last.groups()] if last else []
        for split in (parts, lettered):
            if split:
                units = [Unit("", v) for v in split]
                alternatives.append([*parsed.units[: parsed.main], *units, *parsed.units[parsed.main + 1 :]])
    depth, units = 0, parsed.units
    for candidate in alternatives:
        d = max((_depth(path, candidate) for _, path, _, _ in rows), default=0)
        if d > parsed.main and d > depth:
            depth, units = d, candidate
    if not depth:
        return ResolvedCitation(parsed.cited, instrument_ids, [], exact=False)
    inside = [pid for pid, path, index_unit, _ in rows if index_unit and _depth(path, units) >= depth]
    return ResolvedCitation(parsed.cited, instrument_ids, inside, exact=depth >= len(units))
