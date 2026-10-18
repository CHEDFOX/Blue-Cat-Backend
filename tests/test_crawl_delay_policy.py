"""The request pace per source follows the publisher's own allowance (rate research of 2026-10-18).

delay = max(the publisher's robots.txt Crawl-delay for the host, the manifest's crawl_delay_seconds); a manifest may go
below the old 5 s only down to the project-wide 1 s, and only where the publisher states no rate of its own.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
import yaml

from ulis.catalog import Catalog
from ulis.ops.loading import plan_delay
from ulis.ops.sources import manifest_info
from ulis.settings import REPO_ROOT
from ulis.sources.connectors import polite_http
from ulis.sources.connectors.boe_consolidada import BoeConsolidadaConnector
from ulis.sources.connectors.egov_laws import EgovLawsConnector
from ulis.sources.connectors.eu_cellar import CellarConnector
from ulis.sources.connectors.finlex import FinlexConnector
from ulis.sources.connectors.gesetze_im_internet import GesetzeImInternetConnector
from ulis.sources.connectors.legislation_gov_uk import LegislationGovUkConnector
from ulis.sources.connectors.local_manifest import ManifestError
from ulis.sources.connectors.polite_http import manifest_crawl_delay
from ulis.sources.connectors.riksdagen import RiksdagenSfsConnector

MANIFESTS = REPO_ROOT / "config" / "manifests"

# source id, connector, the delay the research permits (seconds), the shortest delay the connector accepts
RESEARCHED: list[tuple[str, Any, float, float]] = [
    ("eu-eur-lex", CellarConnector, 1.0, 1.0),
    ("es-boe-consolidada", BoeConsolidadaConnector, 1.0, 1.0),
    ("de-gesetze-im-internet", GesetzeImInternetConnector, 1.0, 1.0),
    ("se-riksdagen-sfs", RiksdagenSfsConnector, 1.0, 1.0),
    ("fi-finlex", FinlexConnector, 1.0, 1.0),
    # Digital Agency asked API users not to loop over every law: the connector keeps 5 s as its own minimum.
    ("jp-egov-laws", EgovLawsConnector, 5.0, 5.0),
    # robots.txt Crawl-delay 5 and the fair-use policy's "Follow the crawl-delay setting": 5 s in the manifest.
    ("gb-legislation-gov-uk", LegislationGovUkConnector, 5.0, 1.0),
]


def _small_copy(tmp_path: Path, source_id: str, **changes: Any) -> Path:
    """The committed manifest with its main list cut to three entries (the settings unchanged unless overridden)."""
    data = yaml.safe_load((MANIFESTS / f"{source_id}.yaml").read_text(encoding="utf-8"))
    key = max((k for k, v in data.items() if isinstance(v, list)), key=lambda k: len(data[k]))
    data[key] = data[key][:3]
    data.update(changes)
    path = tmp_path / f"{source_id}.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


@pytest.mark.parametrize(("source_id", "connector", "permitted", "minimum"), RESEARCHED, ids=[r[0] for r in RESEARCHED])
def test_each_committed_manifest_sets_the_permitted_delay_and_validates(
    tmp_path: Path, source_id: str, connector: Any, permitted: float, minimum: float
) -> None:
    path = MANIFESTS / f"{source_id}.yaml"
    manifest = connector(path, source_id, tmp_path)._manifest()  # noqa: SLF001 - the connector's own validation
    assert manifest["crawl_delay_seconds"] == permitted
    assert manifest_crawl_delay(manifest, minimum=minimum) == permitted
    # The header quotes the evidence for the pace: a publisher URL and the date of the reading.
    header = path.read_text(encoding="utf-8").split("\nschema_version:", 1)[0]
    assert "2026-10-18" in header and "https://" in header


@pytest.mark.parametrize(("source_id", "connector", "permitted", "minimum"), RESEARCHED, ids=[r[0] for r in RESEARCHED])
def test_each_connector_refuses_a_delay_below_its_floor(
    tmp_path: Path, source_id: str, connector: Any, permitted: float, minimum: float
) -> None:
    for too_short in (0.5, minimum - 0.5):
        path = _small_copy(tmp_path, source_id, crawl_delay_seconds=too_short)
        with pytest.raises(ManifestError, match="crawl_delay_seconds"):
            connector(path, source_id, tmp_path)._manifest()  # noqa: SLF001
    accepted = _small_copy(tmp_path, source_id, crawl_delay_seconds=minimum)
    assert connector(accepted, source_id, tmp_path)._manifest()["crawl_delay_seconds"] == minimum  # noqa: SLF001


class _Response(io.BytesIO):
    status = 200
    headers: dict[str, str] = {"Content-Type": "application/zip"}

    def __init__(self, data: bytes, url: str):
        super().__init__(data)
        self._url = url

    def geturl(self) -> str:
        return self._url

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: Any) -> None:
        pass


def test_a_connector_lowered_to_one_second_still_obeys_a_robots_crawl_delay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gesetze im Internet at the manifest's 1 s: if the site's robots.txt asked for 4 s, every pause is 4 s."""
    requested: list[str] = []

    def urlopen(request: Any, timeout: float) -> _Response:
        requested.append(request.full_url)
        if request.full_url.endswith("/robots.txt"):
            return _Response(b"User-agent: *\nCrawl-delay: 4\n", request.full_url)
        return _Response(b"PK\x03\x04 a zip", request.full_url)

    monkeypatch.setattr(polite_http.urllib.request, "urlopen", urlopen)
    sleeps: list[float] = []
    manifest = _small_copy(tmp_path, "de-gesetze-im-internet", crawl_delay_seconds=1)
    connector = GesetzeImInternetConnector(manifest, "de-gesetze-im-internet", tmp_path / "cache", sleep=sleeps.append)
    docs = list(connector.fetch())
    assert len(docs) == 3 and requested[0] == "https://www.gesetze-im-internet.de/robots.txt"
    assert len(requested) == 4 and len(sleeps) == 3 and all(3.5 < s <= 4.0 for s in sleeps)


def test_the_dry_run_uses_the_delay_the_connector_will_use(tmp_path: Path, catalog: Catalog) -> None:
    se = manifest_info(catalog.sources.get("se-riksdagen-sfs"), REPO_ROOT)
    assert se is not None and se.crawl_delay == 1 and "data.riksdagen.se" in se.data_hosts
    assert plan_delay(se) == 1.0
    # A Crawl-delay in a robots.txt the connector cached for the manifest's host raises the estimate ...
    (tmp_path / "data.riksdagen.se").mkdir()
    (tmp_path / "data.riksdagen.se" / "robots.txt").write_text("User-agent: *\nCrawl-delay: 3\n")
    assert plan_delay(se, tmp_path) == 3.0
    # ... a host named only in a comment of the manifest does not.
    eu = manifest_info(catalog.sources.get("eu-eur-lex"), REPO_ROOT)
    assert eu is not None and "eur-lex.europa.eu" not in eu.data_hosts
    (tmp_path / "eur-lex.europa.eu").mkdir()
    (tmp_path / "eur-lex.europa.eu" / "robots.txt").write_text("User-agent: *\nCrawl-delay: 10\n")
    assert plan_delay(eu, tmp_path) == 1.0
    gb = manifest_info(catalog.sources.get("gb-legislation-gov-uk"), REPO_ROOT)
    assert gb is not None and plan_delay(gb) == 5.0
