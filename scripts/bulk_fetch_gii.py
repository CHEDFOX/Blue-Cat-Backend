"""Politely download every law listed in Gesetze im Internet's table of contents into the cache.

Phase 1 of a bulk load (phase 2 is `python -m ulis.cli ingest de-gesetze-im-internet` after
`scripts/build_gii_manifest.py`). One request every 5 s, resumable (cached files are not fetched
again), tolerant of 404s (recorded and skipped). Uses the same cache layout as the connector.

    python scripts/bulk_fetch_gii.py [--limit N]
"""

from __future__ import annotations

import re
import sys

from ulis.settings import REPO_ROOT
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp

HOST = "www.gesetze-im-internet.de"
UA = "ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)"
CACHE = REPO_ROOT / "var" / "sources"


def toc_abbreviations(http: PoliteHttp) -> list[str]:
    got = http.get(f"https://{HOST}/gii-toc.xml")
    if got is None:
        raise SystemExit("table of contents not found")
    text = got[0].decode("utf-8", errors="replace")
    return sorted(set(re.findall(r"<link>https?://www\.gesetze-im-internet\.de/([^/<>]+)/xml\.zip</link>", text)))


def main(limit: int | None) -> int:
    http = PoliteHttp(CACHE, UA, allowed_hosts={HOST}, crawl_delay=5.0)
    abbrs = toc_abbreviations(http)
    print(f"{len(abbrs)} laws in the table of contents", flush=True)
    done = failed = 0
    log = REPO_ROOT / "var" / "gii_bulk_status.txt"
    for n, abbr in enumerate(abbrs, 1):
        if limit is not None and n > limit:
            break
        url = f"https://{HOST}/{abbr}/xml.zip"
        try:
            got = http.get(url)
            ok = got is not None and got[0].startswith(b"PK\x03\x04")
        except FetchError as exc:
            print(f"FAILED {abbr}: {exc}", flush=True)
            if "403" in str(exc) or "429" in str(exc):
                print("refusal received: stopping", flush=True)
                return 2
            ok = False
        done += ok
        failed += not ok
        if n % 50 == 0:
            log.write_text(f"{n}/{len(abbrs)} processed, {done} ok, {failed} failed\n")
            print(f"{n}/{len(abbrs)} processed, {done} ok, {failed} failed", flush=True)
    log.write_text(f"finished: {done} ok, {failed} failed of {len(abbrs)}\n")
    print(f"finished: {done} ok, {failed} failed", flush=True)
    return 0


if __name__ == "__main__":
    lim = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None
    sys.exit(main(lim))
