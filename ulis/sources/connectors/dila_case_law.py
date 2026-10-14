"""Fetches French court decisions from the DILA case-law open data (Licence Ouverte 2.0).

Databases (``https://echanges.dila.gouv.fr/OPENDATA/<DB>/``, same layout and terms as LEGI, see ``dila_dump``):

* ``CONSTIT`` - decisions of the Conseil constitutionnel (all, since 1958);
* ``CASS`` - decisions of the Cour de cassation published in the Bulletin; ``INCA`` - its unpublished decisions;
* ``JADE`` - decisions of the Conseil d'Etat, the cours administratives d'appel and the tribunaux administratifs.

Each archive holds one XML file per decision (``.../TEXT/NN/NN/NN/NN/NN/<ID>.xml``). The manifest
(``config/manifests/fr-dila-*.yaml``) names the stock and a selection (courts, years, natures). The connector yields
each
selected decision file **exactly as published**:

1. the archives are read **newest first** (the daily archives published after the stock, then the stock), so the latest
   published version of a decision is the one yielded; an older version of a decision already seen is skipped, and
   decisions named in an archive's ``liste_suppression_<db>.dat`` are not yielded from older archives;
2. the stock is downloaded once (resumable, cached, 5 s between requests, robots.txt read first) and read as a stream;
3. a decision is selected by the court (``JURIDICTION``), the year of ``DATE_DEC`` and the ``NATURE`` in its header;
   ``max_documents`` stops the run after that many decisions (for trial runs).

The text of the decisions is pseudonymised by the publisher (the data sheets: names of natural persons are
pseudonymised following the CNIL recommendation of 29 November 2001); ULIS stores it as published.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ulis.sources.connectors.base import RawDocument
from ulis.sources.connectors.dila_dump import (
    HOST,
    USER_AGENT,
    Archive,
    DilaArchives,
    archive_from_name,
    iter_tar_files,
)
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import FetchError

# The stocks group decisions in folders (cass/global/civile/JURI/TEXT/..., jade/global/publie/CETA/TEXT/...).
MEMBER = re.compile(r"(?:^|/)[a-z]+/global/(?:[A-Za-z_]+/)*TEXT/(?:\d{2}/){5}(?P<id>[A-Z]{8}\d{12})\.xml$")
DELETED = re.compile(r"(?P<id>[A-Z]{8}\d{12})(?:\.xml)?$")
HEADER_BYTES = 6000

__all__ = ["CaseLawManifest", "DilaCaseLawConnector", "FetchError"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class StockSpec(_Strict):
    file: str
    date: date
    bytes: int = Field(gt=0)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class IncrementalSpec(_Strict):
    enabled: bool = False
    through: date | None = None


class CaseSelect(_Strict):
    # Exact JURIDICTION values, or prefixes when they end with "*" ("Cour administrative d'appel*"). Empty: every court.
    juridictions: list[str] = Field(default_factory=list)
    year_from: int | None = None
    year_to: int | None = None
    natures: list[str] = Field(default_factory=list)  # NATURE codes (DC, QPC...); empty: all
    max_documents: int | None = Field(default=None, gt=0)


class CaseLawManifest(_Strict):
    schema_version: Literal[1]
    source_id: str
    database: Literal["CONSTIT", "CASS", "INCA", "JADE"]
    user_agent: str
    crawl_delay_seconds: float = Field(ge=5.0)
    stock: StockSpec
    incrementals: IncrementalSpec = IncrementalSpec()
    select: CaseSelect = CaseSelect()


def header_fields(content: bytes) -> dict[str, str | None]:
    head = content[:HEADER_BYTES].decode("utf-8", "replace")

    def one(tag: str) -> str | None:
        found = re.search(rf"<{tag}>([^<]*)</{tag}>", head)
        return found.group(1).strip() if found else None

    return {"juridiction": one("JURIDICTION"), "date": one("DATE_DEC"), "nature": one("NATURE")}


class DilaCaseLawConnector:
    def __init__(
        self,
        manifest_path: Path,
        expected_source_id: str,
        cache_dir: Path,
        *,
        offline: bool = False,
        sleep: Any = time.sleep,
    ):
        self.manifest_path = manifest_path
        self.expected_source_id = expected_source_id
        self.cache_dir = cache_dir
        self.offline = offline
        self._sleep = sleep
        self.fetch_errors: list[str] = []
        self.archives: DilaArchives | None = None
        self.report: dict[str, Any] = {}

    def manifest(self) -> CaseLawManifest:
        try:
            manifest = CaseLawManifest.model_validate(yaml.safe_load(self.manifest_path.read_text(encoding="utf-8")))
        except (OSError, ValidationError, yaml.YAMLError) as exc:
            raise ManifestError(f"{self.manifest_path}: {exc}") from exc
        if manifest.source_id != self.expected_source_id:
            raise ManifestError("manifest source_id mismatch")
        if manifest.user_agent != USER_AGENT:
            raise ManifestError(f"user_agent must be {USER_AGENT!r}")
        stock = archive_from_name(manifest.stock.file, manifest.database)
        if stock is None or stock.kind != "stock":
            raise ManifestError(f"unexpected stock file name {manifest.stock.file!r} for {manifest.database}")
        if stock.date != manifest.stock.date:
            raise ManifestError("stock.date does not match the stock file name")
        sel = manifest.select
        if sel.year_from and sel.year_to and sel.year_from > sel.year_to:
            raise ManifestError("select.year_from is after select.year_to")
        for nature in sel.natures:
            if not re.fullmatch(r"[A-Z0-9_]{1,16}", nature):
                raise ManifestError(f"invalid nature {nature!r}")
        return manifest

    @staticmethod
    def _wanted(select: CaseSelect, head: dict[str, str | None]) -> bool:
        if select.juridictions:
            juri = head["juridiction"] or ""
            if not any(juri.startswith(j[:-1]) if j.endswith("*") else juri == j for j in select.juridictions):
                return False
        if select.natures and head["nature"] not in select.natures:
            return False
        if select.year_from or select.year_to:
            year = int(head["date"][:4]) if head["date"] and head["date"][:4].isdigit() else None
            if (
                year is None
                or (select.year_from and year < select.year_from)
                or (select.year_to and year > select.year_to)
            ):
                return False
        return True

    def fetch(self) -> Iterator[RawDocument]:
        manifest = self.manifest()
        db = manifest.database
        archives = DilaArchives(
            db, self.cache_dir, offline=self.offline, crawl_delay=manifest.crawl_delay_seconds, sleep=self._sleep
        )
        self.archives = archives
        archives.check_robots()
        stock = archive_from_name(manifest.stock.file, db)
        assert stock is not None
        series: list[Archive] = []
        if manifest.incrementals.enabled:
            series = [
                a
                for a in archives.listing()
                if a.kind == "increment"
                and a.stamp > stock.stamp
                and (manifest.incrementals.through is None or a.date <= manifest.incrementals.through)
            ]
        # Newest first: the latest published version of a decision wins; the stock comes last.
        order = sorted(series, key=lambda a: a.stamp, reverse=True) + [stock]
        seen: set[str] = set()
        counts = {"archives": 0, "members": 0, "selected": 0, "superseded": 0, "deleted_ids": 0}
        limit = manifest.select.max_documents
        for archive in order:
            path = archives.ensure(archive.name, manifest.stock.bytes if archive is stock else None)
            counts["archives"] += 1
            retrieved = datetime.fromtimestamp(path.stat().st_mtime, UTC)
            url = f"https://{HOST}/OPENDATA/{db}/{archive.name}"
            deleted: set[str] = set()
            for name, _size, read in iter_tar_files(path):
                if "suppression" in name and name.endswith(".dat"):
                    deleted.update(
                        m["id"] for line in read().decode("utf-8", "replace").split() if (m := DELETED.search(line))
                    )
                    continue
                match = MEMBER.search(name)
                if match is None:
                    continue
                counts["members"] += 1
                ident = match["id"]
                if ident in seen:
                    counts["superseded"] += 1
                    continue
                content = read()
                # The newest copy decides, selected or not. A decision listed twice in one archive (the CASS stock
                # does so for some) keeps the first copy: the pipeline refuses two different texts for one decision.
                seen.add(ident)
                if not self._wanted(manifest.select, header_fields(content)):
                    continue
                counts["selected"] += 1
                yield RawDocument(
                    external_id=ident,
                    uri=f"{url}#{ident}",
                    media_type="application/xml",
                    content=content,
                    retrieved_at=retrieved,
                    metadata={
                        "database": db,
                        "archive": {"name": archive.name, "date": archive.date.isoformat(), "url": url},
                        "stock": {"name": manifest.stock.file, "date": manifest.stock.date.isoformat()},
                    },
                )
                if limit is not None and counts["selected"] >= limit:
                    self.report = {**counts, "stopped": "max_documents"}
                    return
            seen.update(deleted)  # older archives must not bring a deleted decision back
            counts["deleted_ids"] += len(deleted)
        self.report = counts
