"""Regenerate config/manifests/us-uscode-house.yaml from the OLRC download page.

Reads https://uscode.house.gov/download.html (robots.txt first), takes the current release point, the date it is
"current through" and every main title that has an XML archive (the page's own list: titles 1 to 54 without the
reserved 53; appendices are not main titles and are not listed), downloads each archive with the polite
connector (identifying User-Agent, 5 s apart, cached) and writes the manifest with the SHA-256 of every archive.

    python scripts/build_uscode_manifest.py [--cache-dir var/sources] [--out config/manifests/us-uscode-house.yaml]
                                            [--offline]

A titles' ``positive_law`` flag is the page's asterisk ("This title has been enacted as positive law"). The XML
carries the same fact (``is-positive-law``); ``tests/test_uscode_house.py`` compares the two for the fixtures.
A new release point also needs its date in ``USC_RELEASE_POINT_DATES`` (ulis/sources/parsers/uslm.py).
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import tempfile
from datetime import date
from pathlib import Path

import yaml

from ulis.sources.connectors.moj_laws import robots_allows
from ulis.sources.connectors.polite_http import FetchError, PoliteHttp
from ulis.sources.connectors.uscode_house import (
    BASE_URL,
    HOST,
    UscodeHouseConnector,
    parse_download_page,
    title_url,
)

ROOT = Path(__file__).resolve().parent.parent
USER_AGENT = "ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)"
DELAY = 5

HEADER = """\
# United States Code, all titles, USLM XML from the Office of the Law Revision Counsel (OLRC).
# One archive per main title and release point:
#   https://uscode.house.gov/download/releasepoints/us/pl/<congress>/<law>/xml_usc<NN>@<congress>-<law>.zip
# Release point {release}: "All files are current through {through}" - download page
# https://uscode.house.gov/download.html (read {today}). The XML itself says only "Online@{release}", so the
# parser holds that date (USC_RELEASE_POINT_DATES in ulis/sources/parsers/uslm.py).
# Listed: every main title of the Code that the page offers ({count} titles: 1 to 54 without the reserved title 53).
# Not listed: the appendices to titles 5, 11, 18, 28 and 50 (xml_usc05a ... xml_usc50a; "any Appendix to this title has
# not been enacted as part of the title") and the other formats (XHTML, PCC, PDF).
# `positive_law` is the download page's asterisk: "This title has been enacted as positive law" ({positive} titles).
# For the other {other} titles the Code is "prima facie evidence" of the law (1 U.S.C. 204), and the Statutes at Large
# control; the XML of such a title says is-positive-law "no". The parser checks the XML, not this flag.
# `sha256` pins the exact download (taken on {today}); a later regeneration of a file by the OLRC fails loudly.
# Regenerate with scripts/build_uscode_manifest.py.
"""


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cache-dir", type=Path, default=ROOT / "var" / "sources")
    ap.add_argument("--out", type=Path, default=ROOT / "config" / "manifests" / "us-uscode-house.yaml")
    ap.add_argument("--offline", action="store_true", help="use only what is cached")
    args = ap.parse_args(argv)

    http = PoliteHttp(args.cache_dir, USER_AGENT, allowed_hosts={HOST}, crawl_delay=DELAY, offline=args.offline)
    robots = http.get(f"{BASE_URL}/robots.txt", cache_key="robots.txt")
    if robots is None:
        raise FetchError("robots.txt not found; refusing to fetch without reading it")
    page_url = f"{BASE_URL}/download.html"
    if not robots_allows(robots[0].decode("utf-8", errors="replace"), USER_AGENT, page_url):
        raise FetchError(f"{page_url} is disallowed by robots.txt")
    if not args.offline:
        http.cache_path(page_url).unlink(missing_ok=True)  # the point of the script is the live page
        http.cache_path(page_url).with_name("download.html.meta.json").unlink(missing_ok=True)
    got = http.get(page_url)
    if got is None:
        raise FetchError(f"{page_url}: not found")
    page = parse_download_page(got[0].decode("utf-8"))
    print(f"release point {page.release_point}, {page.current_through}, {len(page.titles)} titles")

    old = {}
    if args.out.exists():
        for entry in yaml.safe_load(args.out.read_text(encoding="utf-8")).get("titles", []):
            old[entry["number"]] = entry.get("sha256")

    draft = {
        "schema_version": 1,
        "source_id": "us-uscode-house",
        "base_url": BASE_URL,
        "user_agent": USER_AGENT,
        "crawl_delay_seconds": DELAY,
        "release_point": {"id": page.release_point, "current_through": page.current_through},
        "titles": [{"number": t["number"], "name": t["name"], "positive_law": t["positive_law"]} for t in page.titles],
    }
    pins: dict[int, str] = {}
    with tempfile.TemporaryDirectory() as tmp:
        draft_path = Path(tmp) / "draft.yaml"
        draft_path.write_text(yaml.safe_dump(draft, sort_keys=False), encoding="utf-8")
        connector = UscodeHouseConnector(draft_path, "us-uscode-house", args.cache_dir, offline=args.offline)
        for doc in connector.fetch():
            number = int(doc.metadata["title"])
            pins[number] = hashlib.sha256(doc.content).hexdigest()
            changed = old.get(number) not in (None, pins[number])
            print(
                f"title {number:>2}: {len(doc.content):>10,} bytes {pins[number][:12]}{'  CHANGED' if changed else ''}"
            )
        for error in connector.fetch_errors:
            print("ERROR", error)
    missing = [t["number"] for t in page.titles if t["number"] not in pins]
    if missing:
        print(f"not written: no archive for titles {missing}")
        return 1

    positive = sum(1 for t in page.titles if t["positive_law"])
    lines = [
        HEADER.format(
            release=page.release_point,
            through=page.current_through,
            today=date.today().isoformat(),
            count=len(page.titles),
            positive=positive,
            other=len(page.titles) - positive,
        ),
        "schema_version: 1",
        "source_id: us-uscode-house",
        f"base_url: {BASE_URL}",
        f'user_agent: "{USER_AGENT}"',
        f"crawl_delay_seconds: {DELAY}",
        "release_point:",
        f'  id: "{page.release_point}"',
        f'  current_through: "{page.current_through}"',
        "titles:",
    ]
    for t in page.titles:
        flow = yaml.safe_dump(
            {"number": t["number"], "name": t["name"], "positive_law": t["positive_law"], "sha256": pins[t["number"]]},
            default_flow_style=True,
            sort_keys=False,
            width=10_000,
        ).strip()
        lines.append(f"  - {flow}")
    args.out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    # Every URL the manifest implies must be one the connector would build.
    for t in page.titles:
        title_url(page.release_point, t["number"])
    print(f"wrote {args.out} ({len(page.titles)} titles, {positive} positive law)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
