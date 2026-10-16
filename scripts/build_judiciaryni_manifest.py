"""Write config/manifests/gb-ni-judiciary-judgments.yaml from the listing of the Judiciary of Northern Ireland.

    python scripts/build_judiciaryni_manifest.py

``https://www.judiciaryni.uk/judicial-decisions?page=N`` lists the site's judicial decisions and directions, about 20
a page,
newest first, back to 1968 (not disallowed by https://www.judiciaryni.uk/robots.txt, which closes ``/search/`` and
``/publications/*type|date``). The decisions with a neutral citation have a page name ``<year>-<series>-<number>`` (for
example ``2026-nica-38``); those are the judgments, sentencing remarks and rulings the manifest lists. Practice
directions,
guidance notes, rules and bulletin items (pages named after their title or a bulletin reference) are counted and left
out.
The decisions themselves are fetched by the connector, two requests each (page and PDF).
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from datetime import UTC, datetime

from ulis.settings import REPO_ROOT
from ulis.sources.connectors.moj_laws import robots_allows
from ulis.sources.connectors.ni_judiciary import SLUG
from ulis.sources.connectors.polite_http import PoliteHttp

USER_AGENT = "ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)"
HOST = "www.judiciaryni.uk"
OUT = REPO_ROOT / "config" / "manifests" / "gb-ni-judiciary-judgments.yaml"
ITEM = re.compile(r'href="/judicial-decisions/([^"/?#]+)"')
FILTER_PAGES = {"date", "judiciary", "type"}


def main() -> int:
    http = PoliteHttp(REPO_ROOT / "var" / "sources", USER_AGENT, allowed_hosts={HOST}, crawl_delay=5.0)
    robots = http.get(f"https://{HOST}/robots.txt", cache_key="robots.txt")
    robots_txt = robots[0].decode("utf-8", errors="replace") if robots else ""
    slugs: dict[str, None] = {}
    others: dict[str, None] = {}
    page = 0
    last = None
    while True:
        url = f"https://{HOST}/judicial-decisions?page={page}"
        if not robots_allows(robots_txt, USER_AGENT, url):
            print(f"robots.txt disallows {url}", file=sys.stderr)
            return 1
        got = http.get(url)
        if got is None:
            print(f"{url}: not found", file=sys.stderr)
            return 1
        text = got[0].decode("utf-8", errors="replace")
        if last is None:
            pages = [int(n) for n in re.findall(r"[?&]page=(\d+)", text)]
            last = max(pages) if pages else 0
        for name in ITEM.findall(text):
            if name in FILTER_PAGES:
                continue
            (slugs if SLUG.match(name) else others)[name] = None
        if page >= last:
            break
        page += 1
        if page % 50 == 0:
            print(f"page {page}/{last}: {len(slugs)} judgments, {len(others)} other items", flush=True)
    listed = sorted(slugs, key=lambda s: (-int(s[:4]), s))
    by_series = Counter(s.split("-")[1] for s in listed)
    by_year = Counter(s[:4] for s in listed)
    today = datetime.now(UTC).date().isoformat()
    lines = [
        "# Decisions of the courts of Northern Ireland published by the Judiciary of Northern Ireland: the pages",
        "# https://www.judiciaryni.uk/judicial-decisions/<year>-<series>-<number> with a neutral citation,",
        "# from the listing",
        f"# https://www.judiciaryni.uk/judicial-decisions?page=N (read {today}: {last + 1} pages).",
        f"# {len(listed)} decisions: "
        + ", ".join(f"{s} {n}" for s, n in sorted(by_series.items(), key=lambda kv: -kv[1]))
        + ".",
        "# By year: " + ", ".join(f"{y} {n}" for y, n in sorted(by_year.items())) + ".",
        f"# Left out: {len(others)} items without a neutral citation in the page name",
        "# (practice directions, notes, rules, bulletin items).",
        f"# Scale: {last + 1} listing requests, then two requests per decision (page and PDF) at 5 s",
        f"# = {2 * len(listed) * 5 / 3600:.1f} hours.",
        "schema_version: 1",
        "source_id: gb-ni-judiciary-judgments",
        f"base_url: https://{HOST}",
        f'user_agent: "{USER_AGENT}"',
        "crawl_delay_seconds: 5",
        "decisions:",
        *[f"  - {s}" for s in listed],
    ]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        f"{len(listed)} decisions written to {OUT} ({http.requests_made} network requests); "
        f"{len(others)} other items left out"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
