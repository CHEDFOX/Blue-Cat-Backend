"""Write config/manifests/tw-moj-laws.yaml from the sitemap that the site's robots.txt allows.

    python -m scripts.build_moj_manifest

https://law.moj.gov.tw/robots.txt has ``Disallow: /`` with three exceptions: ``/News/NewsDetail.aspx``,
``/LawClass/LawAll.aspx`` and ``/SearchEngineSitemap.aspx``, and names that sitemap as its ``Sitemap:``. The
sitemap lists every law and regulation page (``LawAll.aspx?pcode=<PCode>``, one URL per law) next to the news
pages (``NewsDetail.aspx?msgid=...``, not laws and not read). The script checks the sitemap URL against
robots.txt (the connector's own RFC 9309 matcher), fetches it once (``PoliteHttp``: User-Agent, 5 s delay,
cache ``var/sources/law.moj.gov.tw/``) and lists the PCodes in the order of the sitemap.

The hand-picked laws above the ``# --- generated`` marker of the existing manifest are kept as they are.
"""

from __future__ import annotations

import re
import time
from collections import Counter

from ulis.settings import REPO_ROOT
from ulis.sources.connectors.moj_laws import BASE_URL, HOST, law_url, robots_allows, tls_context
from ulis.sources.connectors.polite_http import PoliteHttp

SITEMAP = f"{BASE_URL}/SearchEngineSitemap.aspx"
USER_AGENT = "ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)"
OUT = REPO_ROOT / "config" / "manifests" / "tw-moj-laws.yaml"
CACHE = REPO_ROOT / "var" / "sources"
MARKER = "# --- generated from the sitemap by scripts/build_moj_manifest.py (do not edit below) ---"
_LOC = re.compile(r"<loc>([^<]+)</loc>")
_LAW = re.compile(r"^" + re.escape(BASE_URL) + r"/LawClass/LawAll\.aspx\?pcode=([A-Z][0-9]{7})$")


def main() -> int:
    http = PoliteHttp(
        CACHE, USER_AGENT, allowed_hosts={HOST}, crawl_delay=5.0, sleep=time.sleep, ssl_context=tls_context()
    )
    robots = http.get(f"{BASE_URL}/robots.txt", cache_key="robots.txt")
    if robots is None:
        raise SystemExit("robots.txt not found")
    robots_txt = robots[0].decode("utf-8", errors="replace")
    if not robots_allows(robots_txt, USER_AGENT, SITEMAP):
        raise SystemExit(f"{SITEMAP} is disallowed by robots.txt")
    got = http.get(SITEMAP, cache_key="SearchEngineSitemap.aspx.xml")
    if got is None:
        raise SystemExit(f"{SITEMAP}: not found")
    urls = _LOC.findall(got[0].decode("utf-8"))
    pcodes: list[str] = []
    other: Counter[str] = Counter()
    for url in urls:
        match = _LAW.match(url)
        if match is None:
            other[url.split("?")[0].removeprefix(BASE_URL)] += 1
        elif match.group(1) not in pcodes:
            pcodes.append(match.group(1))
    for pcode in pcodes:
        if not robots_allows(robots_txt, USER_AGENT, law_url(pcode)):
            raise SystemExit(f"{law_url(pcode)} is disallowed by robots.txt")

    lines = OUT.read_text(encoding="utf-8").splitlines()
    cut = lines.index(MARKER) if MARKER in lines else len(lines)
    start = lines.index("laws:") + 1
    head, curated = lines[:start], lines[start:cut]
    taken = {m.group(1) for line in curated if (m := re.search(r"pcode: ([A-Z][0-9]{7})", line))}
    rest = [f"  - {{pcode: {p}}}" for p in pcodes if p not in taken]
    OUT.write_text("\n".join(head + curated + [MARKER] + rest) + "\n", encoding="utf-8")
    by_letter = Counter(p[0] for p in pcodes)
    print(
        f"sitemap: {len(urls)} URLs, {len(pcodes)} laws ({dict(sorted(by_letter.items()))}); other pages: {dict(other)}"
    )
    print(f"{len(taken)} curated, {len(rest)} generated; {http.requests_made} requests")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
