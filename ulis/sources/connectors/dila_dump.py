"""Shared access to the DILA open-data archives (``https://echanges.dila.gouv.fr/OPENDATA/<DB>/``).

The Direction de l'information légale et administrative (DILA) publishes each legal database (LEGI, JADE, CASS,
INCA, CONSTIT, ...) as a directory holding

* one **stock**, ``Freemium_<db>_global_<YYYYMMDD>-<HHMMSS>.tar.gz``: the whole database at that moment, and
* **incremental** archives, ``<DB>_<YYYYMMDD>-<HHMMSS>.tar.gz``: the files created or changed since the previous
  archive (plus a ``liste_suppression_<db>.dat`` of deleted files).

Rules built in (see ``docs/onboarding-a-jurisdiction.md`` and ``docs/jurisdictions/fr.md``):

* the host's ``robots.txt`` is read first and every path is checked against it (the host serves none, a 404, which
  means no restriction); the identifying User-Agent of :mod:`polite_http` is used for every request;
* a pause of at least ``crawl_delay`` (5 s) between requests; back-off on 429/5xx; any other refusal
  (401/403/451...) stops the run, nothing is worked around;
* the archives are downloaded **once** into the cache (``<cache>/echanges.dila.gouv.fr/OPENDATA/<DB>/``), streamed to
  disk and never held in memory, resumed with a ``Range`` request after an interruption; later runs read the cache;
* the archives are read as streams (:func:`iter_tar_files`); nothing is extracted to disk by member name.
"""

from __future__ import annotations

import re
import shutil
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, datetime
from http.client import HTTPException
from pathlib import Path
from typing import Any
from urllib.robotparser import RobotFileParser

from ulis.sources.connectors.polite_http import FetchError, PoliteHttp

HOST = "echanges.dila.gouv.fr"
BASE_PATH = "/OPENDATA"
USER_AGENT = "ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)"
STAMP = r"(?P<stamp>\d{8}-\d{6})"
CHUNK = 1 << 20


@dataclass(frozen=True)
class Archive:
    name: str
    kind: str  # "stock" | "increment"
    stamp: datetime  # from the file name (the publisher's clock, no zone stated)

    @property
    def date(self) -> date:
        return self.stamp.date()


def parse_stamp(text: str) -> datetime:
    return datetime.strptime(text, "%Y%m%d-%H%M%S")  # noqa: DTZ007 - the publisher states no zone


def archive_from_name(name: str, database: str) -> Archive | None:
    stock = re.fullmatch(rf"Freemium_{re.escape(database.lower())}_global_{STAMP}\.tar\.gz", name)
    if stock:
        return Archive(name, "stock", parse_stamp(stock["stamp"]))
    inc = re.fullmatch(rf"{re.escape(database.upper())}_{STAMP}\.tar\.gz", name)
    if inc:
        return Archive(name, "increment", parse_stamp(inc["stamp"]))
    return None


def parse_listing(html: str, database: str) -> list[Archive]:
    """Archives named in an Apache directory index, oldest first."""
    names = re.findall(r'<a href="([^"?/]+)">', html)
    found = {a.name: a for n in names if (a := archive_from_name(urllib.parse.unquote(n), database))}
    return sorted(found.values(), key=lambda a: (a.stamp, a.name))


def iter_tar_files(path: Path) -> Iterator[tuple[str, int, Callable[[], bytes]]]:
    """Stream the regular files of a ``.tar.gz``: ``(member name, size, read)``.

    ``read()`` returns the member's bytes and may only be called before the generator is advanced (the
    archive is read as a stream, in order, without extracting anything to disk)."""
    with tarfile.open(path, mode="r|gz") as tar:
        for member in tar:
            if not member.isfile():
                continue

            def read(member: tarfile.TarInfo = member, tar: tarfile.TarFile = tar) -> bytes:
                handle = tar.extractfile(member)
                return handle.read() if handle is not None else b""

            yield member.name, member.size, read


class DilaArchives:
    """The archives of one DILA database, with a polite cached listing and resumable downloads."""

    def __init__(
        self,
        database: str,
        cache_dir: Path,
        *,
        offline: bool = False,
        crawl_delay: float = 5.0,
        sleep: Any = time.sleep,
        timeout: float = 120.0,
    ):
        self.database = database.upper()
        self.cache_dir = cache_dir
        self.offline = offline
        self.crawl_delay = crawl_delay
        self._sleep = sleep
        self.timeout = timeout
        self.dir = cache_dir / HOST / BASE_PATH.strip("/") / self.database
        self.http = PoliteHttp(
            cache_dir, USER_AGENT, allowed_hosts={HOST}, crawl_delay=crawl_delay, offline=offline, sleep=sleep
        )
        self._robots: RobotFileParser | None = None
        self._last_request = 0.0
        self.downloads = 0

    # ------------------------------------------------------------------ robots.txt and listing
    def check_robots(self) -> None:
        """Read ``/robots.txt`` once and refuse paths it disallows for our agent (a 404 means no rules)."""
        if self._robots is not None:
            return
        parser = RobotFileParser()
        fetched = self.http.get(f"https://{HOST}/robots.txt")
        parser.parse(fetched[0].decode("utf-8", "replace").splitlines() if fetched else [])
        self._robots = parser

    def _allowed(self, path: str) -> None:
        self.check_robots()
        assert self._robots is not None
        if not self._robots.can_fetch(USER_AGENT, f"https://{HOST}{path}"):
            raise FetchError(f"robots.txt of {HOST} disallows {path}")

    @property
    def directory_url(self) -> str:
        return f"https://{HOST}{BASE_PATH}/{self.database}/"

    def listing(self, *, refresh: bool = True) -> list[Archive]:
        """The archives the publisher lists. Online runs fetch a fresh index (one request); offline runs use the
        copy cached by an earlier run (or none)."""
        self._allowed(f"{BASE_PATH}/{self.database}/")
        key = f"OPENDATA/{self.database}-index.html"  # beside the archive directory, not inside it
        cached = self.http.cache_path(self.directory_url, key)
        if refresh and not self.offline:
            for stale in (cached, cached.with_name(cached.name + ".meta.json")):
                stale.unlink(missing_ok=True)
        elif self.offline and not cached.exists():
            return []
        fetched = self.http.get(self.directory_url, key)
        if fetched is None:
            raise FetchError(f"{self.directory_url} not found")
        return parse_listing(fetched[0].decode("utf-8", "replace"), self.database)

    # ------------------------------------------------------------------ downloads
    def path(self, name: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+\.tar\.gz", name):
            raise FetchError(f"unexpected archive name {name!r}")
        return self.dir / name

    def _pause(self) -> None:
        wait = self._last_request + self.crawl_delay - time.monotonic()
        if wait > 0:
            self._sleep(wait)
        self._last_request = time.monotonic()

    def ensure(self, name: str, expected_bytes: int | None = None) -> Path:
        """The local path of an archive, downloading it (once, resumable) when it is not cached yet."""
        dest = self.path(name)
        if dest.exists():
            if expected_bytes is not None and dest.stat().st_size != expected_bytes:
                raise FetchError(f"{dest} has {dest.stat().st_size} bytes, the manifest pins {expected_bytes}")
            return dest
        self._allowed(f"{BASE_PATH}/{self.database}/{name}")
        if self.offline:
            raise FetchError(f"offline and not cached: {name}")
        dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        part = dest.with_name(name + ".part")
        url = f"https://{HOST}{BASE_PATH}/{self.database}/{urllib.parse.quote(name)}"
        total = expected_bytes
        for attempt in range(40):
            have = part.stat().st_size if part.exists() else 0
            if total is not None and have >= total:
                break
            self._pause()
            self.downloads += 1
            headers = {"User-Agent": USER_AGENT}
            if have:
                headers["Range"] = f"bytes={have}-"
            request = urllib.request.Request(url, headers=headers)  # noqa: S310 - https to one host only
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:  # noqa: S310
                    if have and response.status != 206:
                        have = 0  # the server ignored the range: start again
                    if response.status == 206:
                        total = int(response.headers["Content-Range"].rpartition("/")[2])
                    elif response.headers.get("Content-Length"):
                        total = int(response.headers["Content-Length"])
                    with part.open("ab" if have else "wb") as handle:
                        shutil.copyfileobj(response, handle, CHUNK)
            except urllib.error.HTTPError as exc:
                if exc.code == 416 and total is not None:  # nothing left to send: the file is complete
                    break
                if exc.code in (429, 500, 502, 503, 504):
                    retry_after = exc.headers.get("Retry-After")
                    self._sleep(float(retry_after) if retry_after and retry_after.isdigit() else self.crawl_delay * 2)
                    continue
                raise FetchError(f"{url}: HTTP {exc.code} (not retried: a refusal is respected)") from exc
            except (urllib.error.URLError, OSError, HTTPException):
                self._sleep(min(self.crawl_delay * 2 ** min(attempt, 4), 60.0))
                continue
        size = part.stat().st_size if part.exists() else 0
        if total is None or size != total or (expected_bytes is not None and size != expected_bytes):
            raise FetchError(f"{name}: downloaded {size} bytes of {total or expected_bytes} - run again to resume")
        part.replace(dest)
        return dest
