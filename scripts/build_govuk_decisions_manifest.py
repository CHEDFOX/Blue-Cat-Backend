"""Write config/manifests/gb-govuk-tribunal-decisions.yaml from GOV.UK's Search API.

    python scripts/build_govuk_decisions_manifest.py

For each collection (Employment Appeal Tribunal decisions, Upper Tribunal Administrative Appeals Chamber decisions) the
Search API (``https://www.gov.uk/api/search.json?filter_document_type=<type>&count=1000&start=N&fields=link``,
documented at
https://docs.publishing.service.gov.uk/repos/search-api/using-the-search-api.html) lists the pages of the collection,
oldest
first. The decisions themselves are fetched by the connector from the Content API, one request each. Other collections
of
tribunal decisions on GOV.UK are not listed: Employment Tribunal decisions (about 135,000, private individuals),
residential
property tribunal decisions (about 18,000, with addresses in the titles), asylum support decisions (names of asylum
seekers) and
Upper Tribunal Tax and Chancery decisions (no text in the Content API, PDF only).
"""

from __future__ import annotations

import json
import sys
import urllib.parse
from datetime import UTC, datetime

from ulis.settings import REPO_ROOT
from ulis.sources.connectors.govuk_decisions import COLLECTIONS, PATH
from ulis.sources.connectors.moj_laws import robots_allows
from ulis.sources.connectors.polite_http import PoliteHttp

USER_AGENT = "ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)"
HOST = "www.gov.uk"
OUT = REPO_ROOT / "config" / "manifests" / "gb-govuk-tribunal-decisions.yaml"
PAGE = 1000


def main() -> int:
    http = PoliteHttp(REPO_ROOT / "var" / "sources", USER_AGENT, allowed_hosts={HOST}, crawl_delay=5.0)
    robots = http.get(f"https://{HOST}/robots.txt", cache_key="robots.txt")
    robots_txt = robots[0].decode("utf-8", errors="replace") if robots else ""
    listed: dict[str, list[str]] = {}
    totals: dict[str, int] = {}
    for collection, document_type in COLLECTIONS.items():
        paths: list[str] = []
        start = 0
        while True:
            query = {
                "filter_document_type": document_type,
                "count": PAGE,
                "start": start,
                "order": "public_timestamp",
                "fields": "link",
            }
            url = f"https://{HOST}/api/search.json?{urllib.parse.urlencode(query)}"
            if not robots_allows(robots_txt, USER_AGENT, url):
                print(f"robots.txt disallows {url}", file=sys.stderr)
                return 1
            got = http.get(url)
            if got is None:
                print(f"{url}: not found", file=sys.stderr)
                return 1
            data = json.loads(got[0])
            totals[collection] = int(data["total"])
            results = data.get("results", [])
            paths += [str(r["link"]).strip("/") for r in results]
            start += PAGE
            if not results or start >= totals[collection]:
                break
        bad = [p for p in paths if not PATH.match(p) or not p.startswith(collection + "/")]
        if bad:
            print(f"{collection}: {len(bad)} unusual page names not listed, e.g. {bad[:3]}", file=sys.stderr)
        listed[collection] = sorted({p for p in paths if p not in bad})
        print(f"{collection}: {totals[collection]} in the index, {len(listed[collection])} listed", flush=True)
    today = datetime.now(UTC).date().isoformat()
    n = sum(len(v) for v in listed.values())
    lines = [
        "# Tribunal decisions published on GOV.UK whose Content API response holds the text of the decision",
        f"# (listed {today} from https://www.gov.uk/api/search.json): "
        + ", ".join(f"{c} {len(v)}" for c, v in listed.items())
        + f" = {n} decisions.",
        "# Not listed: Employment Tribunal decisions (about 135,000 decisions on private individuals),",
        "# residential property tribunal decisions (about 18,000, addresses in the titles), asylum support decisions,",
        "# and Upper Tribunal Tax and Chancery decisions (the Content API has no text for them).",
        f"# Scale: one request per decision at 5 s = {n * 5 / 3600:.1f} hours.",
        "schema_version: 1",
        "source_id: gb-govuk-tribunal-decisions",
        f"base_url: https://{HOST}",
        f'user_agent: "{USER_AGENT}"',
        "crawl_delay_seconds: 5",
        "decisions:",
        *[f"  - {p}" for c in COLLECTIONS for p in listed[c]],
    ]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{n} decisions written to {OUT} ({http.requests_made} network requests)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
