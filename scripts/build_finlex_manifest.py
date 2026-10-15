"""Write config/manifests/fi-finlex.yaml: every consolidated statute in force that has text, from Finlex's own lists.

    python -m scripts.build_finlex_manifest            # list, intersect, write the manifest
    python -m scripts.build_finlex_manifest --pages 3  # first three list pages only (a test; nothing is written)

Two of the publisher's own indexes are combined, because neither alone says what ULIS needs:

* The **API list** ``GET /akn/fi/act/statute-consolidated/list?isInForce=true&langAndVersion=fin@latest`` names
  every statute in force (about 36,000 on 2026-10-15), but in pages of at most 10 and with no total (about 3,600
  requests), and most of them are empty shells: ``<hcontainer name="contentAbsent"/>``, a title and no text.
* The **download package** ``https://www.finlex.fi/files/open-data/archives/statute-consolidated.zip`` (4.1 GB,
  348,144 members: every statute in both languages with every temporal version) is not downloaded; its central
  directory (42.6 MB at the end of the file) is read with one HTTP Range request and gives the size of every
  document. In the sample the shells are 3.3-5.3 KB and the smallest document with text 7.3 KB, so a latest
  version of at least ``MIN_TEXT_BYTES`` is taken to have text. The same sizes say which language a statute has
  text in.

The manifest lists the statutes that are in the API list AND have text in at least one language, with
``languages`` restricted to the languages that have text. Pages are cached under ``var/sources/`` (one request per
5 s, User-Agent set, 429 backed off), so an interrupted run resumes where it stopped. robots.txt of both hosts is
read and the paths used are checked against it.

The hand-picked works above the ``# --- generated`` marker of the existing manifest are kept as they are
(including their ``history`` flags); every other work is appended with ``history: false`` (the latest consolidated
version only).
"""

from __future__ import annotations

import json
import re
import struct
import sys
import time
from collections import defaultdict

from ulis.settings import REPO_ROOT
from ulis.sources.connectors.moj_laws import robots_allows
from ulis.sources.connectors.polite_http import PoliteHttp

API_HOST = "opendata.finlex.fi"
SITE_HOST = "www.finlex.fi"
LIST = f"https://{API_HOST}/finlex/avoindata/v1/akn/fi/act/statute-consolidated/list"
ARCHIVE = f"https://{SITE_HOST}/files/open-data/archives/statute-consolidated.zip"
USER_AGENT = "ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)"
OUT = REPO_ROOT / "config" / "manifests" / "fi-finlex.yaml"
CACHE = REPO_ROOT / "var" / "sources"
MARKER = (
    "# --- generated from the list endpoint and the archive index by scripts/build_finlex_manifest.py"
    " (do not edit below) ---"
)
PAGE = 10  # the API's maximum page size
MIN_TEXT_BYTES = 5_500  # documents without text ("contentAbsent") are 3.3-5.3 KB; the smallest with text is 7.3 KB
_ENTRY = re.compile(r"/statute-consolidated/(\d{4})/(\d{1,5}(?:-\d{3})?)/(fin|swe)@")
_MEMBER = re.compile(r"^akn/fi/act/statute-consolidated/(\d{4})/(\d{1,5}(?:-\d{3})?)/(fin|swe)@(\d*)/main\.xml$")


def head_and_curated() -> tuple[list[str], list[str]]:
    """The manifest's header lines (to ``works:``) and the curated work lines before the marker."""
    lines = OUT.read_text(encoding="utf-8").splitlines()
    cut = lines.index(MARKER) if MARKER in lines else len(lines)
    start = lines.index("works:") + 1
    return lines[:start], lines[start:cut]


def archive_text_sizes(http: PoliteHttp) -> dict[tuple[int, str], dict[str, int]]:
    """(year, number) -> {language: size of its latest document} from the archive's central directory."""
    robots = http.get(f"https://{SITE_HOST}/robots.txt", cache_key="robots.txt")
    if robots is None or not robots_allows(robots[0].decode("utf-8", "replace"), USER_AGENT, ARCHIVE):
        raise SystemExit(f"{ARCHIVE}: robots.txt of {SITE_HOST} is missing or disallows it")
    tail = http.get(ARCHIVE, cache_key="archive-tail/statute-consolidated.tail64k", headers={"Range": "bytes=-65536"})
    if tail is None:
        raise SystemExit(f"{ARCHIVE}: not found")
    data = tail[0]
    eocd64 = data.rfind(b"PK\x06\x06")
    if eocd64 < 0:
        raise SystemExit("the archive's zip64 end-of-central-directory record is not in its last 64 KB")
    (_sig, _size, _made, _need, _d1, _d2, _n_disk, entries, cd_size, cd_offset) = struct.unpack(
        "<IQHHIIQQQQ", data[eocd64 : eocd64 + 56]
    )
    cache_key = f"archive-tail/statute-consolidated.centraldir-{cd_offset}-{cd_size}"
    got = http.get(ARCHIVE, cache_key=cache_key, headers={"Range": f"bytes={cd_offset}-{cd_offset + cd_size - 1}"})
    if got is None:
        raise SystemExit(f"{ARCHIVE}: central directory not found")
    cd = got[0]
    members: dict[tuple[int, str, str], list[tuple[str, int]]] = defaultdict(list)
    pos = count = 0
    while pos < len(cd) and cd[pos : pos + 4] == b"PK\x01\x02":
        fields = struct.unpack("<IHHHHHHIIIHHHHHII", cd[pos : pos + 46])
        usize, nlen, elen, clen = fields[9], fields[10], fields[11], fields[12]
        name = cd[pos + 46 : pos + 46 + nlen].decode("utf-8", "replace")
        extra = cd[pos + 46 + nlen : pos + 46 + nlen + elen]
        e = 0
        while e + 4 <= len(extra):  # zip64 extended information: the real sizes when the 32-bit fields are saturated
            header_id, size = struct.unpack("<HH", extra[e : e + 4])
            if header_id == 1 and usize == 0xFFFFFFFF:
                usize = struct.unpack("<Q", extra[e + 4 : e + 12])[0]
            e += 4 + size
        m = _MEMBER.match(name)
        if m:
            members[(int(m.group(1)), m.group(2), m.group(3))].append((m.group(4), usize))
        pos += 46 + nlen + elen + clen
        count += 1
    if count != entries:
        raise SystemExit(f"read {count} central directory entries, the archive says {entries}")
    sizes: dict[tuple[int, str], dict[str, int]] = defaultdict(dict)
    for (year, number, lang), versions in members.items():
        current = [size for version, size in versions if version == ""]
        # "fin@" is the current version; a statute without one is known by its (only or newest) version number
        sizes[(year, number)][lang] = current[0] if current else max(versions)[1]
    return sizes


def list_in_force(http: PoliteHttp, max_pages: int | None) -> tuple[set[tuple[int, str]], int]:
    """Statutes in force with a Finnish version, from the API's list endpoint (10 per page, no total)."""
    works: set[tuple[int, str]] = set()
    page = 1
    while max_pages is None or page <= max_pages:
        url = f"{LIST}?format=json&page={page}&limit={PAGE}&sortBy=number&isInForce=true&langAndVersion=fin%40latest"
        got = http.get(url, cache_key=f"list/statute-consolidated/inforce-fin-p{page:05d}.json")
        if got is None:
            raise SystemExit(f"{url}: not found")
        entries = json.loads(got[0])
        for entry in entries:
            m = _ENTRY.search(entry["akn_uri"])
            if m is None or m.group(3) != "fin":
                raise SystemExit(f"unexpected list entry {entry!r}")
            works.add((int(m.group(1)), m.group(2)))
        if page % 100 == 0:
            print(f"page {page}: {len(works)} statutes in force", flush=True)
        if len(entries) < PAGE:
            break
        page += 1
    return works, min(page, max_pages) if max_pages is not None else page


def main() -> int:
    max_pages = int(sys.argv[sys.argv.index("--pages") + 1]) if "--pages" in sys.argv else None
    api = PoliteHttp(CACHE, USER_AGENT, allowed_hosts={API_HOST}, crawl_delay=5.0, sleep=time.sleep)
    site = PoliteHttp(CACHE, USER_AGENT, allowed_hosts={SITE_HOST}, crawl_delay=5.0, sleep=time.sleep)
    sizes = archive_text_sizes(site)
    in_force, pages = list_in_force(api, max_pages)
    head, curated = head_and_curated()
    taken = set()
    for line in curated:
        m = re.search(r"year: (\d{4}), number: \"([^\"]+)\"", line)
        if m:
            taken.add((int(m.group(1)), m.group(2)))
    lines = []
    shells = absent = both = 0
    for year, number in sorted(in_force, key=lambda w: (w[0], w[1].zfill(9))):
        if (year, number) in taken:
            continue
        langs = sizes.get((year, number))
        if langs is None:
            absent += 1  # in the API list, not in the archive (published since the archive was built)
            langs = {"fin": MIN_TEXT_BYTES, "swe": MIN_TEXT_BYTES}
        with_text = sorted(lang for lang, size in langs.items() if size >= MIN_TEXT_BYTES)
        if not with_text:
            shells += 1
            continue
        both += with_text == ["fin", "swe"]
        extra = "" if with_text == ["fin", "swe"] else f", languages: [{with_text[0]}]"
        lines.append(f'  - {{year: {year}, number: "{number}", history: false{extra}}}')
    text = "\n".join(head + curated + [MARKER] + lines) + "\n"
    if max_pages is None:  # a partial listing must not overwrite the committed manifest
        OUT.write_text(text, encoding="utf-8")
    raw = sum(
        size
        for (year, number), langs in sizes.items()
        if (year, number) in in_force
        for size in langs.values()
        if size >= MIN_TEXT_BYTES
    )
    print(
        f"archive: {len(sizes)} statutes; list: {len(in_force)} in force ({pages} pages); "
        f"{shells} in force without text; {absent} not in the archive; {len(lines)} listed "
        f"({both} in both languages), {len(taken)} curated; latest documents of the listed ones: {raw} bytes; "
        f"{api.requests_made + site.requests_made} requests"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
