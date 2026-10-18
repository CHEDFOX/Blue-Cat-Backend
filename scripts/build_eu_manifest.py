# ruff: noqa: E501 - the generated manifest header is written as long lines
"""Write config/manifests/eu-eur-lex.yaml: every regulation, directive and decision in force that Cellar holds in a
machine-readable text format in English.

    python -m scripts.build_eu_manifest

Everything comes from the Publications Office's SPARQL endpoint (https://publications.europa.eu/webapi/rdf/sparql, the
service the dataset "Sparql and RESTful API access to European Union law and publications (CELLAR)" offers to
re-users; op.europa.eu/robots.txt allows it), one pair of queries per year of the act, cached under
``var/sources/publications.europa.eu/``:

* the works with ``cdm:resource_legal_in-force`` true whose CELEX number is ``3YYYY[RLD]NNNN`` (sector 3: regulations,
  directives, decisions; corrigenda and other suffixed numbers are not acts) that have an English language version, with
  the manifestation types of that version. Each is listed in the best text format Cellar has (``TEXT_FORMATS``): Formex 4
  (``fmx4``), else the OJ XHTML (``xhtml``), else the legacy EUR-Lex HTML (``html``, the format of most acts of 1953-2003);
  acts held only as PDF or print files are counted in the header, not listed;
* the consolidated versions (``cdm:act_consolidated_based_on_resource_legal``) of those works that apply from today or
  earlier, with their manifestation types. The latest one with a Formex 4 file (greatest ``cdm:act_consolidated_date``) is
  listed *instead of* the act's own Official Journal text: it is the text in force, with the amendments applied. Acts
  without such a version are listed with their Official Journal text as adopted. ``--all-consolidated-versions`` lists
  every dated consolidated version instead of the latest (about 6.5 times as many documents for the acts that have any).

English only for the bulk (``languages: [en]``); the core acts (the Treaties, the Charter and five instruments of
the pilot) keep English, French and German. ``sparql_batch`` makes the connector ask for 20 works per query.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.parse
from collections import Counter
from datetime import date
from typing import Any

from ulis.settings import REPO_ROOT
from ulis.sources.connectors.eu_cellar import LANGUAGE_AUTHORITY, SPARQL_URL
from ulis.sources.connectors.polite_http import PoliteHttp

OUT = REPO_ROOT / "config" / "manifests" / "eu-eur-lex.yaml"
CACHE = REPO_ROOT / "var" / "sources"
USER_AGENT = "ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)"
REQUEST_SECONDS = 5.0
BATCH = 20
REQUESTS_PER_WORK = 2.24  # a Formex 4 work: one to three files (measured on 100 works, 2026-10-15)
REQUESTS_PER_TEXT_WORK = 1.31  # an XHTML or HTML work: one file (measured on 48 works, 2026-10-18: 63 requests)
FIRST_YEAR = 1952
PREFIXES = (
    "PREFIX cdm: <http://publications.europa.eu/ontology/cdm#>\nPREFIX xsd: <http://www.w3.org/2001/XMLSchema#>\n"
)
ENGLISH = f"<{LANGUAGE_AUTHORITY}ENG>"
# The acts that were in the pilot manifest. They come first, in the three languages, whatever year they are from.
CORE_TREATIES = [
    ("12016M/TXT", "xhtml", "Treaty on European Union, consolidated version 2016"),
    ("12016E/TXT", "xhtml", "Treaty on the Functioning of the European Union, consolidated version 2016"),
    ("12016P/TXT", "fmx4", "Charter of Fundamental Rights of the European Union (OJ C 202, 2016)"),
]
CORE_ACTS = {
    "32016R0679": "Regulation (EU) 2016/679 (GDPR)",
    "32008R0593": "Regulation (EC) No 593/2008 (Rome I)",
    "32012R1215": "Regulation (EU) No 1215/2012 (Brussels I recast)",
    "32011L0083": "Directive 2011/83/EU (consumer rights)",
    "32024R1689": "Regulation (EU) 2024/1689 (Artificial Intelligence Act)",
}
TYPE_ORDER = {"R": 0, "L": 1, "D": 2}
# Machine-readable text formats the parser reads, in order of preference: Formex 4, the OJ XHTML, the legacy EUR-Lex
# HTML (PDF scans and print files are never used).
TEXT_FORMATS = ("fmx4", "xhtml", "html")


def run(http: PoliteHttp, name: str, query: str) -> list[dict[str, str]]:
    query = PREFIXES + query
    url = SPARQL_URL + "?" + urllib.parse.urlencode({"query": query, "format": "application/sparql-results+json"})
    digest = hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]
    got = http.get(
        url,
        cache_key=f"webapi/rdf/sparql/manifest/{name}-{digest}.json",
        headers={"Accept": "application/sparql-results+json"},
    )
    if got is None:
        raise SystemExit(f"SPARQL endpoint not found for {name}")
    return [{k: v["value"] for k, v in row.items()} for row in json.loads(got[0])["results"]["bindings"]]


def acts_of_year(http: PoliteHttp, year: int) -> dict[str, tuple[str, frozenset[str]]]:
    """CELEX -> (document date, manifestation types of the English version) of the acts in force of that year.

    Every act in force that has an English language version is listed, whatever its formats (``fmx4``, ``xhtml``,
    ``html``, ``pdf``, ``pdfa1a``, ``print`` ...); the caller chooses the format to fetch."""
    rows = run(
        http,
        f"acts-{year}",
        f"""SELECT DISTINCT ?c ?d ?t WHERE {{
  ?w cdm:resource_legal_in-force "true"^^xsd:boolean ; cdm:resource_legal_id_celex ?c .
  FILTER(REGEX(STR(?c), "^3{year}[RLD][0-9]{{4}}$"))
  OPTIONAL {{ ?w cdm:work_date_document ?d }}
  ?e cdm:expression_belongs_to_work ?w ; cdm:expression_uses_language {ENGLISH} .
  ?m cdm:manifestation_manifests_expression ?e ; cdm:manifestation_type ?t .
}}""",
    )
    out: dict[str, tuple[str, set[str]]] = {}
    for r in rows:
        entry = out.setdefault(r["c"], (r.get("d", ""), set()))
        entry[1].add(r["t"])
    return {c: (d, frozenset(types)) for c, (d, types) in out.items()}


def consolidated_of_year(http: PoliteHttp, year: int) -> dict[str, list[tuple[str, str, frozenset[str]]]]:
    """CELEX of an act in force of that year -> [(consolidation date, CELEX of the consolidated version, manifestation
    types of its English version)], for the consolidated versions applying from today or earlier."""
    rows = run(
        http,
        f"consolidated-{year}",
        f"""SELECT DISTINCT ?b ?cc ?cd ?t WHERE {{
  ?bw cdm:resource_legal_in-force "true"^^xsd:boolean ; cdm:resource_legal_id_celex ?b .
  FILTER(REGEX(STR(?b), "^3{year}[RLD][0-9]{{4}}$"))
  ?cw cdm:act_consolidated_based_on_resource_legal ?bw ; cdm:act_consolidated_date ?cd ; cdm:resource_legal_id_celex ?cc .
  ?e cdm:expression_belongs_to_work ?cw ; cdm:expression_uses_language {ENGLISH} .
  ?m cdm:manifestation_manifests_expression ?e ; cdm:manifestation_type ?t .
  FILTER(REGEX(STR(?cc), "^0[0-9]{{4}}[A-Z]{{1,2}}[0-9]{{4}}-[0-9]{{8}}$"))
  FILTER(STR(?cd) <= "{date.today().isoformat()}")
}}""",
    )
    grouped: dict[tuple[str, str, str], set[str]] = {}
    for r in rows:
        grouped.setdefault((r["b"], r["cd"], r["cc"]), set()).add(r["t"])
    out: dict[str, list[tuple[str, str, frozenset[str]]]] = {}
    for (base, cons_date, cons), types in sorted(grouped.items()):
        out.setdefault(base, []).append((cons_date, cons, frozenset(types)))
    return out


def choose_format(types: frozenset[str]) -> str | None:
    """The format to fetch for a work held in these manifestation types: Formex 4 when there is one, else the OJ XHTML,
    else the legacy HTML. None when the English version exists only in formats the parser cannot read (PDF, print)."""
    return next((fmt for fmt in TEXT_FORMATS if fmt in types), None)


def _unlisted_reason(types: frozenset[str]) -> str:
    return "PDF only" if types and all(t.startswith("pdf") for t in types) else "other formats only"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--all-consolidated-versions", action="store_true")
    parser.add_argument("--to-year", type=int, default=date.today().year)
    args = parser.parse_args(argv)

    http = PoliteHttp(
        CACHE, USER_AGENT, allowed_hosts={"publications.europa.eu", "op.europa.eu"}, crawl_delay=REQUEST_SECONDS
    )
    http.get("https://publications.europa.eu/robots.txt")
    acts: dict[str, tuple[str, frozenset[str]]] = {}
    consolidated: dict[str, list[tuple[str, str, frozenset[str]]]] = {}
    for year in range(FIRST_YEAR, args.to_year + 1):
        found = acts_of_year(http, year)
        acts.update(found)
        if found:
            consolidated.update(consolidated_of_year(http, year))
        by_format = Counter(choose_format(types) or "none" for _, types in found.values())
        print(
            f"{year}: {len(found)} acts in force with an English version"
            + (f" ({', '.join(f'{n} {fmt}' for fmt, n in sorted(by_format.items()))})" if found else ""),
            flush=True,
        )

    # The format of each act: Formex 4 when Cellar has it, else XHTML; acts held only as PDF (scans) are not listed.
    formats = {celex: choose_format(types) for celex, (_, types) in acts.items()}
    listed = {celex: fmt for celex, fmt in formats.items() if fmt}
    unlisted = Counter(_unlisted_reason(acts[celex][1]) for celex, fmt in formats.items() if fmt is None)
    unlisted_types = Counter(", ".join(sorted(acts[celex][1])) for celex, fmt in formats.items() if fmt is None)

    entries: list[dict[str, Any]] = []
    for celex, fmt, label in CORE_TREATIES:
        entries.append({"celex": celex, "format": fmt, "languages": ["en", "fr", "de"], "_label": label})
    core_cons = 0
    n_cons = n_adopted = n_versions = 0
    adopted_by_format: Counter[str] = Counter()
    cons_xhtml_only = 0
    order = sorted(listed, key=lambda c: (0 if c in CORE_ACTS else 1, TYPE_ORDER[c[5]], -int(c[1:5]), c))
    for celex in order:
        fmt = listed[celex]
        # A consolidated version is read from Formex 4 only (its dates come from INFO.CONSLEG).
        versions = sorted((cd, cc) for cd, cc, types in consolidated.get(celex, []) if "fmx4" in types)
        if not versions and any("xhtml" in types for _, _, types in consolidated.get(celex, [])):
            cons_xhtml_only += 1
        core = celex in CORE_ACTS
        extra: dict[str, Any] = {"languages": ["en", "fr", "de"]} if core else {}
        if versions:
            chosen = versions if args.all_consolidated_versions else versions[-1:]
            for _, cons in chosen:
                entries.append({"celex": cons, "format": "fmx4", "base": celex, **extra})
                n_versions += 1
            n_cons += 1
            core_cons += core
        else:
            entries.append({"celex": celex, "format": fmt, **extra})
            n_adopted += 1
            adopted_by_format[fmt] += 1
    for celex in CORE_ACTS:
        if celex not in listed:
            print(
                f"warning: core act {celex} is not among the acts in force with an English text file", file=sys.stderr
            )

    by_type = Counter(c[5] for c in listed)
    by_format_type = {fmt: Counter(c[5] for c, f in listed.items() if f == fmt) for fmt in TEXT_FORMATS}
    # Three batched SPARQL queries per 20 works and one request per file: a Formex 4 work is one to three files (224
    # requests for 100 works, 2026-10-15), an XHTML or HTML work one file (63 requests for 48 works, 2026-10-18).
    n_text_entries = sum(1 for e in entries if e["format"] in ("xhtml", "html"))
    requests = round((len(entries) - n_text_entries) * REQUESTS_PER_WORK + n_text_entries * REQUESTS_PER_TEXT_WORK)
    names = {"R": "regulations", "L": "directives", "D": "decisions"}

    def types_line(counter: Counter[str]) -> str:
        return ", ".join(f"{counter[k]:,} {names[k]}" for k in "RLD")

    def total(fmt: str) -> int:
        return sum(by_format_type[fmt].values())

    header = "\n".join(
        [
            "# EU law fetched from Cellar, the Publications Office repository behind EUR-Lex: every regulation, directive and",
            "# decision in force (cdm:resource_legal_in-force) that has a machine-readable text file in English - Formex 4, else",
            "# the OJ XHTML, else the legacy EUR-Lex HTML (format: fmx4 / xhtml / html) - plus the Treaties and the Charter.",
            "# Reuse: Commission Decision 2011/833/EU (source acknowledged; see config/sources/eu-eur-lex.yaml).",
            f"# Generated by `python -m scripts.build_eu_manifest` on {date.today().isoformat()}; regenerate to refresh (the set of acts in force changes daily).",
            "# Polite access: identifying User-Agent, at least 5 s between requests, local cache.",
            "#",
            f"# Scale: {len(listed):,} acts in force ({types_line(by_type)}): {total('fmx4'):,} with an English Formex 4 file",
            f"# ({types_line(by_format_type['fmx4'])}), {total('xhtml'):,} held in English as OJ XHTML without Formex 4 ({types_line(by_format_type['xhtml'])})",
            f"# and {total('html'):,} as legacy EUR-Lex HTML only ({types_line(by_format_type['html'])}).",
            f"# {n_cons:,} acts have a consolidated version in Formex 4, listed instead of the Official Journal text ({n_versions:,} consolidated documents"
            f"{' - every dated version' if args.all_consolidated_versions else ' - the latest version applying today'});",
            f"# {n_adopted:,} are listed with their text as adopted ({adopted_by_format['fmx4']:,} Formex 4, {adopted_by_format['xhtml']:,} XHTML, {adopted_by_format['html']:,} HTML).",
            f"# {len(entries):,} works, English only except the Treaties, the Charter and five core acts (en, fr, de); about {requests:,} requests",
            f"# at 5 s = {requests * REQUEST_SECONDS / 3600:.0f} h ({REQUESTS_PER_WORK} requests per Formex 4 work and {REQUESTS_PER_TEXT_WORK} per XHTML/HTML work, measured;",
            "# the three queries cover 20 works).",
            f"# Not listed: {sum(unlisted.values()):,} acts in force whose English version has no machine-readable text file"
            f" ({'; '.join(f'{n:,} {reason}' for reason, n in sorted(unlisted.items()))};",
            f"# types seen: {'; '.join(f'{n:,} [{t}]' for t, n in unlisted_types.most_common(6))}), acts with no English version, and acts before {FIRST_YEAR}.",
            f"# Consolidated versions held without Formex 4 are not used ({cons_xhtml_only:,} acts have one as XHTML only; their text as adopted is listed instead).",
            "# Size of the Formex 4 works, estimated from a uniform random sample of 100 (2026-10-15): 22.9 provision versions per work",
            "# (median 4, max 388), so ~615,000 provision versions and ~2 GB of PostgreSQL (3.3 KB per version with its search index);",
            "# raw bundles 49 KB on average (median 28 KB, max 345 KB), ~1.3 GB in the object store.",
            "# Size of the XHTML/HTML works, from a random sample of 48 (37 HTML, 11 XHTML; 2026-10-18): 10.3 provision versions per",
            "# work (median 4, max 90), so ~69,000 provision versions; HTML bundles 48 KB on average (median 25 KB, max 226 KB), XHTML",
            "# bundles 1.5 MB on average (median 123 KB, max 7.8 MB: decisions concluding agreements, with the agreement attached);",
            "# ~0.3 GB of raw bundles and ~1 GB of PostgreSQL (the 48 took 7.4 MB of tables for 2.3 MB of text).",
        ]
    )
    lines = [
        header,
        "schema_version: 1",
        "source_id: eu-eur-lex",
        f'user_agent: "{USER_AGENT}"',
        "crawl_delay_seconds: 5",
        f"sparql_batch: {BATCH}",
        "languages: [en]",
        "works:",
    ]
    for e in entries:
        label = e.pop("_label", None)
        body = ", ".join(f"{k}: {json.dumps(v) if not isinstance(v, list) else json.dumps(v)}" for k, v in e.items())
        lines.append(
            f"  - {{{body}}}"
            + (
                f"   # {label}"
                if label
                else (
                    f"   # {CORE_ACTS[e.get('base') or e['celex']]}"
                    if (e.get("base") or e["celex"]) in CORE_ACTS
                    else ""
                )
            )
        )
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        f"{len(entries)} works written to {OUT.relative_to(REPO_ROOT)} ({len(listed)} acts, {n_cons} with a consolidated version,"
        f" {adopted_by_format['xhtml']} as adopted in XHTML, {adopted_by_format['html']} in HTML; {sum(unlisted.values())} acts not listed)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
