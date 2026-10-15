# ruff: noqa: E501 - the generated manifest header is written as long lines
"""Write config/manifests/eu-eur-lex.yaml: every regulation, directive and decision in force that Cellar holds as Formex 4.

    python -m scripts.build_eu_manifest

Everything comes from the Publications Office's SPARQL endpoint (https://publications.europa.eu/webapi/rdf/sparql, the
service the dataset "Sparql and RESTful API access to European Union law and publications (CELLAR)" offers to
re-users; op.europa.eu/robots.txt allows it), one pair of queries per year of the act, cached under
``var/sources/publications.europa.eu/``:

* the works with ``cdm:resource_legal_in-force`` true whose CELEX number is ``3YYYY[RLD]NNNN`` (sector 3: regulations,
  directives, decisions; corrigenda and other suffixed numbers are not acts) and that have a Formex 4 file in English;
* the consolidated versions (``cdm:act_consolidated_based_on_resource_legal``) of those works that have a Formex 4 file
  in English and apply from today or earlier. The latest one (greatest ``cdm:act_consolidated_date``) is listed *instead of* the act's own Official
  Journal text: it is the text in force, with the amendments applied. Acts without a consolidated version are listed
  with their Official Journal text as adopted. ``--all-consolidated-versions`` lists every dated consolidated version
  instead of the latest (about 6.5 times as many documents for the acts that have any).

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


def acts_of_year(http: PoliteHttp, year: int) -> dict[str, str]:
    """CELEX -> document date of the acts in force of that year with an English Formex 4 file."""
    rows = run(
        http,
        f"acts-{year}",
        f"""SELECT DISTINCT ?c ?d WHERE {{
  ?w cdm:resource_legal_in-force "true"^^xsd:boolean ; cdm:resource_legal_id_celex ?c .
  FILTER(REGEX(STR(?c), "^3{year}[RLD][0-9]{{4}}$"))
  OPTIONAL {{ ?w cdm:work_date_document ?d }}
  ?e cdm:expression_belongs_to_work ?w ; cdm:expression_uses_language {ENGLISH} .
  ?m cdm:manifestation_manifests_expression ?e ; cdm:manifestation_type ?t .
  FILTER(STR(?t) = "fmx4")
}}""",
    )
    return {r["c"]: r.get("d", "") for r in rows}


def consolidated_of_year(http: PoliteHttp, year: int) -> dict[str, list[tuple[str, str]]]:
    """CELEX of an act in force of that year -> [(consolidation date, CELEX of the consolidated version)]."""
    rows = run(
        http,
        f"consolidated-{year}",
        f"""SELECT DISTINCT ?b ?cc ?cd WHERE {{
  ?bw cdm:resource_legal_in-force "true"^^xsd:boolean ; cdm:resource_legal_id_celex ?b .
  FILTER(REGEX(STR(?b), "^3{year}[RLD][0-9]{{4}}$"))
  ?cw cdm:act_consolidated_based_on_resource_legal ?bw ; cdm:act_consolidated_date ?cd ; cdm:resource_legal_id_celex ?cc .
  ?e cdm:expression_belongs_to_work ?cw ; cdm:expression_uses_language {ENGLISH} .
  ?m cdm:manifestation_manifests_expression ?e ; cdm:manifestation_type ?t .
  FILTER(STR(?t) = "fmx4")
  FILTER(REGEX(STR(?cc), "^0[0-9]{{4}}[A-Z]{{1,2}}[0-9]{{4}}-[0-9]{{8}}$"))
  FILTER(STR(?cd) <= "{date.today().isoformat()}")
}}""",
    )
    out: dict[str, list[tuple[str, str]]] = {}
    for r in rows:
        out.setdefault(r["b"], []).append((r["cd"], r["cc"]))
    return out


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--all-consolidated-versions", action="store_true")
    parser.add_argument("--to-year", type=int, default=date.today().year)
    args = parser.parse_args(argv)

    http = PoliteHttp(
        CACHE, USER_AGENT, allowed_hosts={"publications.europa.eu", "op.europa.eu"}, crawl_delay=REQUEST_SECONDS
    )
    http.get("https://publications.europa.eu/robots.txt")
    acts: dict[str, str] = {}
    consolidated: dict[str, list[tuple[str, str]]] = {}
    for year in range(FIRST_YEAR, args.to_year + 1):
        found = acts_of_year(http, year)
        acts.update(found)
        if found:
            consolidated.update(consolidated_of_year(http, year))
        print(f"{year}: {len(found)} acts in force with an English Formex 4 file", flush=True)

    entries: list[dict[str, Any]] = []
    for celex, fmt, label in CORE_TREATIES:
        entries.append({"celex": celex, "format": fmt, "languages": ["en", "fr", "de"], "_label": label})
    core_cons = 0
    n_cons = n_adopted = n_versions = 0
    order = sorted(acts, key=lambda c: (0 if c in CORE_ACTS else 1, TYPE_ORDER[c[5]], -int(c[1:5]), c))
    for celex in order:
        versions = sorted(consolidated.get(celex, []))
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
            entries.append({"celex": celex, "format": "fmx4", **extra})
            n_adopted += 1
    for celex in CORE_ACTS:
        if celex not in acts:
            print(
                f"warning: core act {celex} is not among the acts in force with an English Formex 4 file",
                file=sys.stderr,
            )

    by_type = Counter(c[5] for c in acts)
    languages_per_work = [len(e.get("languages", ["en"])) for e in entries]
    requests = sum(languages_per_work) + 3 * ((len(entries) + BATCH - 1) // BATCH)
    names = {"R": "regulations", "L": "directives", "D": "decisions"}
    header = "\n".join(
        [
            "# EU law fetched from Cellar, the Publications Office repository behind EUR-Lex: every regulation, directive and",
            "# decision in force (cdm:resource_legal_in-force) that has a Formex 4 file in English, plus the Treaties and the",
            "# Charter. Reuse: Commission Decision 2011/833/EU (source acknowledged; see config/sources/eu-eur-lex.yaml).",
            f"# Generated by `python -m scripts.build_eu_manifest` on {date.today().isoformat()}; regenerate to refresh (the set of acts in force changes daily).",
            "# Polite access: identifying User-Agent, at least 5 s between requests, local cache.",
            "#",
            f"# Scale: {len(acts):,} acts in force ({', '.join(f'{by_type[k]:,} {names[k]}' for k in 'RLD')}); of these {n_cons:,} have a consolidated version, listed instead of",
            f"# the Official Journal text ({n_versions:,} consolidated documents{' - every dated version' if args.all_consolidated_versions else ' - the latest version of each act'}), and {n_adopted:,} are listed with their text as adopted.",
            f"# {len(entries):,} works, English only except the Treaties, the Charter and five core acts (en, fr, de); {requests:,} requests at 5 s",
            f"# = {requests * REQUEST_SECONDS / 3600:.0f} h (the files of a work are one to a few requests; the three queries cover 20 works).",
            "# Not listed: the ~7,300 acts in force that Cellar holds only as PDF/HTML scans (no Formex 4), and acts in force before 1952.",
            "# Size, estimated from a uniform random sample (see docs/jurisdictions/eu.md): ESTIMATE",
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
        f"{len(entries)} works written to {OUT.relative_to(REPO_ROOT)} ({len(acts)} acts, {n_cons} with a consolidated version)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
