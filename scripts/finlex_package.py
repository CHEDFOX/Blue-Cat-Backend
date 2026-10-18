"""Keep Finlex's download package and the manifest's pin of it current (fi-finlex, ``mode: package``).

    python -m scripts.finlex_package                   # the cached package, the pin, and the publisher's current one
    python -m scripts.finlex_package --replace --pin   # download the publisher's package if it is newer, then pin it
    python -m scripts.finlex_package --pin             # pin the cached package (after a first download)

Finlex rebuilds ``https://www.finlex.fi/files/open-data/archives/statute-consolidated.zip`` once a day under the same
URL ("the packages are updated with new documents once a day", https://www.finlex.fi/en/open-data/download-materials).
The connector loads only the package the manifest pins (``package.bytes`` and ``package.sha256``), so a full load is
reproducible and a newer package is never mixed in silently. This script asks the publisher what it serves now
(robots.txt, then one HEAD request, with the connector's User-Agent and delay), downloads the new package into the
cache when the ETag differs (``--replace``; the old one is deleted first, one 4.4 GB file at a time) and writes
size, SHA-256, ETag, Last-Modified and retrieval time into ``config/manifests/fi-finlex.yaml`` (``--pin``). A refusal
(401/403/429/451) stops it. Then ``python -m ulis.cli ingest fi-finlex`` loads the new package: documents whose bytes
are unchanged are recognised by their SHA-256 and skipped.
"""

from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.request

from ulis.settings import REPO_ROOT, load_settings
from ulis.sources.connectors.finlex import PACKAGE_HOST, PACKAGE_URL, FinlexConnector
from ulis.sources.connectors.moj_laws import robots_allows
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp, manifest_crawl_delay

MANIFEST = REPO_ROOT / "config" / "manifests" / "fi-finlex.yaml"
_BLOCK = re.compile(r"(?m)^package:\n(?:  .*\n)+")


def publisher_package(manifest: dict[str, object]) -> dict[str, object]:
    """ETag, Last-Modified and size of the package the publisher serves now (robots.txt first, then one HEAD)."""
    user_agent = str(manifest["user_agent"])
    site = PoliteHttp(
        load_settings().source_cache_dir,
        user_agent,
        allowed_hosts={PACKAGE_HOST},
        crawl_delay=manifest_crawl_delay(manifest),
        honour_robots_delay=True,
    )
    robots = site.get(f"https://{PACKAGE_HOST}/robots.txt", refresh=True)
    if robots is not None and not robots_allows(robots[0].decode("utf-8", "replace"), user_agent, PACKAGE_URL):
        raise SystemExit(f"{PACKAGE_URL}: disallowed by robots.txt")
    time.sleep(site.delay_for(PACKAGE_HOST))
    request = urllib.request.Request(PACKAGE_URL, method="HEAD", headers={"User-Agent": user_agent})  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - the fixed https URL
            return {
                "etag": response.headers.get("ETag"),
                "last_modified": response.headers.get("Last-Modified"),
                "bytes": int(response.headers.get("Content-Length") or 0),
            }
    except urllib.error.HTTPError as exc:
        raise SystemExit(f"{PACKAGE_URL}: HTTP {exc.code}; stopping (a refusal is respected)") from exc


def pin_block(meta: dict[str, object]) -> str:
    etag = str(meta.get("etag") or "")
    return (
        "package:\n"
        f"  url: {PACKAGE_URL}\n"
        f"  bytes: {meta['bytes']}\n"
        f"  sha256: {meta['sha256']}\n"
        f"  last_modified: {json.dumps(meta.get('last_modified'))}\n"
        f"  etag: '{etag}'\n"
        f"  retrieved_at: {json.dumps(meta.get('retrieved_at'))}\n"
    )


def main(argv: list[str]) -> int:
    connector = FinlexConnector(MANIFEST, "fi-finlex", load_settings().source_cache_dir)
    manifest = connector._manifest()  # noqa: SLF001 - the connector's own validation
    pin = manifest.get("package") or {}
    cached = connector.package_file()
    meta_file = cached.with_name(cached.name + ".meta.json")
    meta = json.loads(meta_file.read_text()) if cached.exists() and meta_file.exists() else None
    print(f"pinned:    {pin.get('bytes')} bytes, SHA-256 {pin.get('sha256')}, Last-Modified {pin.get('last_modified')}")
    if meta:
        print(f"cached:    {meta['bytes']} bytes, SHA-256 {meta['sha256']}, ETag {meta['etag']}")
    else:
        print("cached:    none")
    current = publisher_package(manifest)
    print(f"publisher: {current['bytes']} bytes, ETag {current['etag']}, Last-Modified {current['last_modified']}")
    if "--replace" in argv and (meta is None or meta.get("etag") != current["etag"]):
        cached.unlink(missing_ok=True)
        meta_file.unlink(missing_ok=True)
        try:
            _, meta = connector.download_package(manifest, check_pin=False)
        except FetchError as exc:
            print(f"download stopped: {exc}", file=sys.stderr)
            return 1
        print(f"downloaded {meta['bytes']} bytes in {meta['download_seconds']} s, SHA-256 {meta['sha256']}")
    if "--pin" in argv:
        if meta is None:
            print("nothing to pin: no package in the cache (use --replace)", file=sys.stderr)
            return 1
        text = MANIFEST.read_text(encoding="utf-8")
        if not _BLOCK.search(text):
            print(f"{MANIFEST} has no package block", file=sys.stderr)
            return 1
        MANIFEST.write_text(_BLOCK.sub(lambda _m: pin_block(meta), text, count=1), encoding="utf-8")
        print(f"pinned {meta['bytes']} bytes, SHA-256 {meta['sha256']} in {MANIFEST.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
