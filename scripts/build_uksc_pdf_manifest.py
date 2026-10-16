"""Write the manifests of the UK Supreme Court and Privy Council judgments from the courts' own sitemaps.

    python scripts/build_uksc_pdf_manifest.py                # config/manifests/gb-uksc-judgments-pdf.yaml
    python scripts/build_uksc_pdf_manifest.py --court jcpc   # gb-jcpc-judgments.yaml and gb-jcpc-judgments-pdf.yaml

The sitemap (https://supremecourt.uk/sitemap.xml, https://jcpc.uk/sitemap.xml) lists one page per appeal,
``/cases/<court>-<year>-<number>`` (2009 to today), and, for the judgments the court publishes in full as HTML, a page
``/cases/judgments/<court>-<year>-<number>``. The PDF manifest lists every case page without an HTML judgment: the
connector fetches the page, skips the appeals that have no judgment yet and reads the judgment PDF of the others.
For the Privy Council the HTML manifest is written too (the HTML manifest of the Supreme Court, ``gb-uksc-judgments``,
is kept as committed: it names the joined appeals it leaves out). One request for the sitemap; the cases are fetched
by the connectors.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from ulis.settings import REPO_ROOT
from ulis.sources.connectors.moj_laws import robots_allows
from ulis.sources.connectors.polite_http import PoliteHttp

USER_AGENT = "ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)"
COURTS = {
    "uksc": ("UKSC", "supremecourt.uk", "gb-uksc-judgments", "UK Supreme Court"),
    "jcpc": ("JCPC", "jcpc.uk", "gb-jcpc-judgments", "Judicial Committee of the Privy Council"),
}


def by_year(items: list[tuple[str, ...]]) -> str:
    return ", ".join(f"{y} {n}" for y, n in sorted(Counter(y for y, _ in items).items()))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--court", choices=sorted(COURTS), default="uksc")
    ap.add_argument("--out-dir", type=Path, default=REPO_ROOT / "config" / "manifests")
    args = ap.parse_args()
    prefix, host, source, court_name = COURTS[args.court]
    case = re.compile(rf"https?://(?:www\.)?{re.escape(host)}/cases/{prefix.lower()}-(\d{{4}})-(\d{{4}})(?=<|\s|$)")
    html_page = re.compile(
        rf"https?://(?:www\.)?{re.escape(host)}/cases/judgments/{prefix.lower()}-(\d{{4}})-(\d{{4}})(?=<|\s|$)"
    )
    http = PoliteHttp(REPO_ROOT / "var" / "sources", USER_AGENT, allowed_hosts={host}, crawl_delay=5.0)
    robots = http.get(f"https://{host}/robots.txt", cache_key="robots.txt")
    robots_txt = robots[0].decode("utf-8", errors="replace") if robots else ""
    url = f"https://{host}/sitemap.xml"
    if not robots_allows(robots_txt, USER_AGENT, url):
        print(f"robots.txt disallows {url}", file=sys.stderr)
        return 1
    got = http.get(url, cache_key="sitemap.xml")
    if got is None:
        print(f"{url}: not found", file=sys.stderr)
        return 1
    locs = re.findall(r"<loc>([^<]+)</loc>", got[0].decode("utf-8", errors="replace"))
    html_done = sorted({m.groups() for loc in locs if (m := html_page.fullmatch(loc))})
    cases = sorted({m.groups() for loc in locs if (m := case.fullmatch(loc))})
    listed = [c for c in cases if c not in set(html_done)]
    today = datetime.now(UTC).date().isoformat()

    def head(source_id: str) -> str:
        return (
            f"schema_version: 1\nsource_id: {source_id}\nbase_url: https://{host}\n"
            f'user_agent: "{USER_AGENT}"\ncrawl_delay_seconds: 5\ncases:\n'
        )

    pdf_comments = [
        f"# Judgments of the {court_name} that the court publishes as PDF: the case pages",
        f"# https://{host}/cases/{prefix.lower()}-<year>-<number> listed in the court's sitemap",
        f"# (https://{host}/sitemap.xml, read {today}) that have no HTML judgment page",
        f"# (/cases/judgments/{prefix.lower()}-<year>-<number>; those are the source {source}).",
        "# Appeals without a judgment yet and joined appeals (one page per case number, the same judgment)",
        "# are skipped by the connector.",
        f"# {len(listed)} case pages ({len(cases)} in the sitemap, {len(html_done)} with an HTML judgment): "
        + by_year(listed),
        "# Scale: one request per case page and one per judgment PDF at 5 s.",
    ]
    out = args.out_dir / f"{source}-pdf.yaml"
    entries = "".join(f"  - {prefix}/{y}/{n}\n" for y, n in listed)
    out.write_text("\n".join(pdf_comments) + "\n" + head(f"{source}-pdf") + entries, encoding="utf-8")
    print(f"{len(listed)} cases written to {out} ({http.requests_made} network requests)")
    if args.court == "jcpc":  # the Privy Council's HTML manifest is generated; the Supreme Court's is kept as committed
        html_comments = [
            f"# Judgments of the {court_name} that the court publishes in full as HTML on its website: the pages",
            f"# https://{host}/cases/judgments/{prefix.lower()}-<year>-<number> listed in the court's sitemap",
            f"# (https://{host}/sitemap.xml, read {today}): {len(html_done)} pages ({by_year(html_done)}).",
            "# Joined appeals have one page per case number with the same judgment; the connector skips a second page",
            "# with a neutral citation it has already passed on.",
            "# Scale: one request per page at 5 s.",
        ]
        out = args.out_dir / f"{source}.yaml"
        entries = "".join(f"  - {prefix}/{y}/{n}\n" for y, n in html_done)
        out.write_text("\n".join(html_comments) + "\n" + head(source) + entries, encoding="utf-8")
        print(f"{len(html_done)} judgment pages written to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
