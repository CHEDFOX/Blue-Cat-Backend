"""Build config/manifests/ch-fedlex.yaml from the Fedlex linked-data (SPARQL) endpoint.

For each work (an SR consolidation abstract, e.g. ``https://fedlex.data.admin.ch/eli/cc/1999/404``)
it lists the consolidations (``jolux:Consolidation``) with their dates of applicability and the
Akoma Ntoso XML files of the German, French and Italian versions:

* the *current* consolidation: the latest one applicable on the build date;
* with ``history``: every earlier consolidation of the same unbroken run (each one applicable
  from the day after the previous one ended, all three languages in XML). An earlier
  consolidation without XML ends the run: the dates before it are a history gap;
* consolidations applicable after the build date (published in advance) are recorded under
  ``scheduled`` and not fetched.

Requests go through PoliteHttp (identifying User-Agent, 5 s between requests, cached).

    python scripts/build_fedlex_manifest.py [YYYY-MM-DD]
"""

from __future__ import annotations

import json
import sys
import urllib.parse
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import yaml

from ulis.sources.connectors.fedlex import DATA_FILESTORE, LANGUAGES, SPARQL_ENDPOINT, USER_AGENT
from ulis.sources.connectors.polite_http import PoliteHttp

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "config" / "manifests" / "ch-fedlex.yaml"
ELI = "https://fedlex.data.admin.ch/eli/cc/"
LANG_CODES = {"DEU": "de", "FRA": "fr", "ITA": "it"}

# (SR number, ELI path, history wanted, label for humans)
WORKS: list[tuple[str, str, bool, str]] = [
    ("101", "1999/404", True, "Federal Constitution (BV / Cst. / Cost.)"),
    ("210", "24/233_245_233", False, "Civil Code (ZGB / CC / CC)"),
    ("220", "27/317_321_377", False, "Code of Obligations (OR / CO / CO)"),
    ("311.0", "54/757_781_799", False, "Criminal Code (StGB / CP / CP)"),
    ("235.1", "2022/491", True, "Federal Act on Data Protection (DSG / LPD / LPD)"),
    ("272", "2010/262", True, "Civil Procedure Code (ZPO / CPC / CPC)"),
    ("172.021", "1969/737_757_755", True, "Federal Act on Administrative Procedure (VwVG / PA / PA)"),
]

QUERY = """PREFIX jolux: <http://data.legilux.public.lu/resource/ontology/jolux#>
SELECT ?work ?applic ?end ?lang ?file WHERE {
  VALUES ?work { %s }
  ?cons a jolux:Consolidation ; jolux:isMemberOf ?work ; jolux:dateApplicability ?applic .
  OPTIONAL { ?cons jolux:dateEndApplicability ?end }
  OPTIONAL {
    ?cons jolux:isRealizedBy ?expr .
    ?expr jolux:language ?lang ; jolux:isEmbodiedBy ?manif .
    ?manif jolux:userFormat <https://fedlex.data.admin.ch/vocabulary/user-format/xml> ;
           jolux:isExemplifiedBy ?file .
  }
}
ORDER BY ?work ?applic"""


def _sparql(http: PoliteHttp, query: str) -> list[dict[str, Any]]:
    url = f"{SPARQL_ENDPOINT}?" + urllib.parse.urlencode({"query": query})
    got = http.get(url, headers={"Accept": "application/sparql-results+json"})
    if got is None:
        raise SystemExit("SPARQL endpoint not found")
    return json.loads(got[0])["results"]["bindings"]  # type: ignore[no-any-return]


def main(build_date: date) -> int:
    http = PoliteHttp(ROOT / "var" / "sources", USER_AGENT, allowed_hosts={"fedlex.data.admin.ch"})
    rows = _sparql(http, QUERY % " ".join(f"<{ELI}{path}>" for _, path, _, _ in WORKS))
    by_work: dict[str, dict[date, dict[str, Any]]] = {}
    for row in rows:
        work = row["work"]["value"]
        start = date.fromisoformat(row["applic"]["value"][:10])
        cons = by_work.setdefault(work, {}).setdefault(start, {"end": None, "files": {}})
        if "end" in row:
            cons["end"] = date.fromisoformat(row["end"]["value"][:10])
        lang = LANG_CODES.get(row.get("lang", {}).get("value", "").rsplit("/", 1)[-1])
        file_url = row.get("file", {}).get("value", "")
        if lang and file_url:
            if not file_url.startswith(DATA_FILESTORE):
                raise SystemExit(f"unexpected file location {file_url}")
            cons["files"][lang] = file_url

    works = []
    for sr, path, history, label in WORKS:
        series = by_work.get(ELI + path)
        if not series:
            raise SystemExit(f"no consolidations for SR {sr}")
        dates = sorted(series)
        applicable = [d for d in dates if d <= build_date]
        current = applicable[-1]
        if set(series[current]["files"]) != set(LANGUAGES):
            raise SystemExit(f"SR {sr}: current consolidation {current} lacks XML in {LANGUAGES}")
        chosen = [current]
        if history:
            for earlier in reversed(applicable[:-1]):
                cons = series[earlier]
                contiguous = cons["end"] is not None and cons["end"] + timedelta(days=1) == chosen[0]
                if not contiguous or set(cons["files"]) != set(LANGUAGES):
                    break  # the run is broken: dates before ``chosen[0]`` are a history gap
                chosen.insert(0, earlier)
        works.append(
            {
                "sr": sr,
                "eli": ELI + path,
                "label": label,
                "history": history,
                "consolidations": [
                    {
                        "date": d.isoformat(),
                        "end": series[d]["end"].isoformat() if series[d]["end"] else None,
                        "files": {lang: series[d]["files"][lang] for lang in LANGUAGES},
                    }
                    for d in chosen
                ],
                "scheduled": [d.isoformat() for d in dates if d > build_date],
            }
        )
    manifest = {
        "schema_version": 1,
        "source_id": "ch-fedlex",
        "built_on": build_date.isoformat(),
        "sparql_endpoint": SPARQL_ENDPOINT,
        "user_agent": USER_AGENT,
        "crawl_delay_seconds": 5,
        "languages": list(LANGUAGES),
        "works": works,
    }
    header = (
        "# Swiss federal law from Fedlex (Classified Compilation SR, Akoma Ntoso XML), de/fr/it.\n"
        "# Generated by scripts/build_fedlex_manifest.py from the Fedlex SPARQL endpoint.\n"
        "# Files are listed under fedlex.data.admin.ch/filestore/ (whose robots.txt disallows that path)\n"
        "# and fetched from the public mapping www.fedlex.admin.ch/filestore/ used by the Fedlex website.\n"
        "# Dies ist keine amtliche Veröffentlichung. Massgebend ist allein die Veröffentlichung durch die\n"
        "# Bundeskanzlei (Art. 49 Abs. 1 Bst. c PublV, SR 170.512.1).\n"
    )
    OUT.write_text(header + yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True), encoding="utf-8")
    total = sum(len(w["consolidations"]) for w in works) * len(LANGUAGES)
    print(f"{len(works)} works, {total} files listed; SPARQL requests made: {http.requests_made}")
    return 0


if __name__ == "__main__":
    sys.exit(main(date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()))
