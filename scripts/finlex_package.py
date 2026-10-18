"""Finlex's download package for fi-finlex (``mode: package``): what is cached, what Finlex serves, and the pin.

    python -m scripts.finlex_package                   # the manifest's pin, the cached package, Finlex's current one
    python -m scripts.finlex_package --replace         # download Finlex's current package if it differs from the cache
    python -m scripts.finlex_package --replace --pin   # ... and pin it exactly (bytes, SHA-256) for a reproducible run
    python -m scripts.finlex_package --live            # back to ``pin: latest`` (the committed default)

Finlex rebuilds ``https://www.finlex.fi/files/open-data/archives/statute-consolidated.zip`` once a day under the same
URL ("the packages are updated with new documents once a day", https://www.finlex.fi/en/open-data/download-materials).
With ``package.pin: latest`` the connector itself loads whatever Finlex serves when a run starts (one HEAD decides
whether the cached copy is still current) and records the package's size and SHA-256 in every document's metadata;
nothing has to be done between runs. ``--pin`` freezes the manifest to one package (``package.pin: exact`` with its
size, SHA-256, ETag, Last-Modified and retrieval time): the connector then refuses any other, so a run can be repeated
exactly while that package is cached (Finlex will not serve it after its next rebuild). Requests: robots.txt, then one
HEAD (and the download with ``--replace``), with the connector's User-Agent and delay; a refusal (401/403/429/451)
stops the script.
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
    """An exact pin of one package (``package.pin: exact``)."""
    etag = str(meta.get("etag") or "")
    return (
        "package:\n"
        f"  url: {PACKAGE_URL}\n"
        "  pin: exact\n"
        f"  bytes: {meta['bytes']}\n"
        f"  sha256: {meta['sha256']}\n"
        f"  last_modified: {json.dumps(meta.get('last_modified'))}\n"
        f"  etag: '{etag}'\n"
        f"  retrieved_at: {json.dumps(meta.get('retrieved_at'))}\n"
    )


def live_block(approx_bytes: int) -> str:
    """The package Finlex serves when a run starts (``package.pin: latest``, the committed default)."""
    return (
        "package:\n"
        f"  url: {PACKAGE_URL}\n"
        "  pin: latest\n"
        f"  approx_bytes: {approx_bytes}  # the package of 2026-10-17, for the load plan only (not checked)\n"
    )


def main(argv: list[str]) -> int:
    connector = FinlexConnector(MANIFEST, "fi-finlex", load_settings().source_cache_dir)
    manifest = connector._manifest()  # noqa: SLF001 - the connector's own validation
    pin = manifest.get("package") or {}
    cached = connector.package_file()
    meta_file = cached.with_name(cached.name + ".meta.json")
    meta = json.loads(meta_file.read_text()) if cached.exists() and meta_file.exists() else None
    if pin.get("pin") == "latest":
        print("manifest:  pin latest (the package Finlex serves when a run starts)")
    else:
        print(f"manifest:  pin exact, {pin.get('bytes')} bytes, SHA-256 {pin.get('sha256')}")
    if meta:
        print(f"cached:    {meta['bytes']} bytes, SHA-256 {meta['sha256']}, ETag {meta['etag']}")
    else:
        print("cached:    none")
    if "--replace" in argv:
        try:
            _, meta = connector.download_package(manifest, check_pin=False)  # one HEAD; downloads only if it differs
        except FetchError as exc:
            print(f"download stopped: {exc}", file=sys.stderr)
            return 1
        print(f"current:   {meta['bytes']} bytes, SHA-256 {meta['sha256']}, ETag {meta['etag']}")
        for event in connector.download_events:
            print(f"           {event}")
    else:
        current = publisher_package(manifest)
        print(f"publisher: {current['bytes']} bytes, ETag {current['etag']}, Last-Modified {current['last_modified']}")
    text = MANIFEST.read_text(encoding="utf-8")
    if ("--pin" in argv or "--live" in argv) and not _BLOCK.search(text):
        print(f"{MANIFEST} has no package block", file=sys.stderr)
        return 1
    if "--pin" in argv:
        if meta is None:
            print("nothing to pin: no package in the cache (use --replace)", file=sys.stderr)
            return 1
        MANIFEST.write_text(_BLOCK.sub(lambda _m: pin_block(meta), text, count=1), encoding="utf-8")
        print(f"pinned {meta['bytes']} bytes, SHA-256 {meta['sha256']} in {MANIFEST.relative_to(REPO_ROOT)}")
    elif "--live" in argv:
        approx = int(pin.get("approx_bytes") or pin.get("bytes") or (meta or {}).get("bytes") or 0)
        MANIFEST.write_text(_BLOCK.sub(lambda _m: live_block(approx), text, count=1), encoding="utf-8")
        print(f"{MANIFEST.relative_to(REPO_ROOT)}: pin latest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
