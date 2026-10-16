"""Write config/manifests/gb-uksc-judgments-pdf.yaml from the UK Supreme Court's own sitemap.

    python scripts/build_uksc_pdf_manifest.py            # reads https://supremecourt.uk/sitemap.xml (cached under
    var/sources)

The sitemap lists one page per appeal, ``/cases/uksc-<year>-<number>`` (2009 to today), and, for the judgments the Court
publishes in full as HTML, a page ``/cases/judgments/uksc-<year>-<number>`` (the source ``gb-uksc-judgments``). Every
case
page without an HTML judgment is listed here: the connector fetches the page, skips the appeals that have no judgment
yet
and reads the judgment PDF of the others. One request for the sitemap; the cases themselves are fetched by the
connector.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from datetime import UTC, datetime

from ulis.settings import REPO_ROOT
from ulis.sources.connectors.moj_laws import robots_allows
from ulis.sources.connectors.polite_http import PoliteHttp

USER_AGENT = "ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)"
HOST = "supremecourt.uk"
OUT = REPO_ROOT / "config" / "manifests" / "gb-uksc-judgments-pdf.yaml"
CASE = re.compile(r"https?://(?:www\.)?supremecourt\.uk/cases/uksc-(\d{4})-(\d{4})(?=<|\s|$)")
HTML_JUDGMENT = re.compile(r"https?://(?:www\.)?supremecourt\.uk/cases/judgments/uksc-(\d{4})-(\d{4})(?=<|\s|$)")


def main() -> int:
    http = PoliteHttp(REPO_ROOT / "var" / "sources", USER_AGENT, allowed_hosts={HOST}, crawl_delay=5.0)
    robots = http.get(f"https://{HOST}/robots.txt", cache_key="robots.txt")
    robots_txt = robots[0].decode("utf-8", errors="replace") if robots else ""
    url = f"https://{HOST}/sitemap.xml"
    if not robots_allows(robots_txt, USER_AGENT, url):
        print(f"robots.txt disallows {url}", file=sys.stderr)
        return 1
    got = http.get(url, cache_key="sitemap.xml")
    if got is None:
        print(f"{url}: not found", file=sys.stderr)
        return 1
    text = got[0].decode("utf-8", errors="replace")
    locs = re.findall(r"<loc>([^<]+)</loc>", text)
    html_done = {m.groups() for loc in locs if (m := HTML_JUDGMENT.fullmatch(loc))}
    cases = sorted({m.groups() for loc in locs if (m := CASE.fullmatch(loc))})
    listed = [c for c in cases if c not in html_done]
    by_year = Counter(y for y, _ in listed)
    today = datetime.now(UTC).date().isoformat()
    lines = [
        "# Judgments of the UK Supreme Court that the Court publishes as PDF: the case pages",
        "# https://supremecourt.uk/cases/uksc-<year>-<number> listed in the Court's sitemap",
        f"# (https://supremecourt.uk/sitemap.xml, read {today}) that have no HTML judgment page",
        "# (/cases/judgments/uksc-<year>-<number>; those are the source gb-uksc-judgments).",
        "# Appeals without a judgment yet and joined appeals (one page per case number, the same judgment)",
        "# are skipped by the connector.",
        f"# {len(listed)} case pages ({len(cases)} in the sitemap, {len(html_done)} with an HTML judgment): "
        + ", ".join(f"{y} {n}" for y, n in sorted(by_year.items())),
        "# Scale: one request per case page and one per judgment PDF at 5 s.",
        "schema_version: 1",
        "source_id: gb-uksc-judgments-pdf",
        f"base_url: https://{HOST}",
        f'user_agent: "{USER_AGENT}"',
        "crawl_delay_seconds: 5",
        "cases:",
        *[f"  - UKSC/{y}/{n}" for y, n in listed],
    ]
    OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(listed)} cases written to {OUT} ({http.requests_made} network requests)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
