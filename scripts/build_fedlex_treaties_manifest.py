"""Build config/manifests/ch-fedlex-treaties.yaml from the Fedlex linked-data (SPARQL) endpoint.

Same method as scripts/build_fedlex_manifest.py, for the treaties listed in
``ulis.sources.parsers.fedlex_treaty.TREATIES``: for each, the current consolidation and every earlier
consolidation of the same unbroken run that has XML in German, French and Italian and follows the
previous one without a gap. Consolidations applicable after the build date are recorded under
``scheduled`` and not fetched. Requests go through PoliteHttp (identifying User-Agent, 5 s between
requests, cached).

    python scripts/build_fedlex_treaties_manifest.py [YYYY-MM-DD]
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
from ulis.sources.parsers.fedlex_treaty import ELI_CC, TREATIES

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "config" / "manifests" / "ch-fedlex-treaties.yaml"
LANG_CODES = {"DEU": "de", "FRA": "fr", "ITA": "it"}

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
    rows = _sparql(http, QUERY % " ".join(f"<{ELI_CC}{path}>" for path in TREATIES))
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
    for path, treaty in TREATIES.items():
        series = by_work.get(ELI_CC + path)
        if not series:
            raise SystemExit(f"no consolidations for SR {treaty.sr}")
        dates = sorted(series)
        applicable = [d for d in dates if d <= build_date]
        current = applicable[-1]
        if set(series[current]["files"]) != set(LANGUAGES):
            raise SystemExit(f"SR {treaty.sr}: current consolidation {current} lacks XML in {LANGUAGES}")
        chosen = [current]
        for earlier in reversed(applicable[:-1]):
            cons = series[earlier]
            contiguous = cons["end"] is not None and cons["end"] + timedelta(days=1) == chosen[0]
            if not contiguous or set(cons["files"]) != set(LANGUAGES):
                break  # the run is broken: dates before ``chosen[0]`` are a history gap
            chosen.insert(0, earlier)
        works.append(
            {
                "sr": treaty.sr,
                "eli": ELI_CC + path,
                "jurisdiction": treaty.jurisdiction,
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
        "source_id": "ch-fedlex-treaties",
        "built_on": build_date.isoformat(),
        "sparql_endpoint": SPARQL_ENDPOINT,
        "user_agent": USER_AGENT,
        "crawl_delay_seconds": 5,
        "languages": list(LANGUAGES),
        "works": works,
    }
    header = (
        "# International treaties as published by Switzerland (Classified Compilation SR 0.x, Akoma Ntoso XML),\n"
        "# de/fr/it. Generated by scripts/build_fedlex_treaties_manifest.py from the Fedlex SPARQL endpoint.\n"
        "# Files are listed under fedlex.data.admin.ch/filestore/ (whose robots.txt disallows that path)\n"
        "# and fetched from the public mapping www.fedlex.admin.ch/filestore/ used by the Fedlex website.\n"
        "# Dies ist keine amtliche Veröffentlichung. Massgebend ist allein die Veröffentlichung durch die\n"
        "# Bundeskanzlei (Art. 49 Abs. 1 Bst. c PublV, SR 170.512.1).\n"
    )
    OUT.write_text(header + yaml.safe_dump(manifest, sort_keys=False, allow_unicode=True), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}: {len(works)} treaties, {sum(len(w['consolidations']) for w in works)} consolidations")
    return 0


if __name__ == "__main__":
    sys.exit(main(date.fromisoformat(sys.argv[1]) if len(sys.argv) > 1 else date.today()))
