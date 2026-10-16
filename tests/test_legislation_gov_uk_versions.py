"""legislation.gov.uk: works that exist in one version, compact manifest entries, history dates, remembered 404s.

The documents here are minimal Akoma Ntoso skeletons (the connector reads only the expression date and the lifecycle
events);
the parser is exercised on real documents in ``test_legislation_gov_uk.py`` and ``test_legislation_gov_uk_wide.py``.
"""

from __future__ import annotations

import io
import urllib.error
from email.message import Message
from pathlib import Path
from typing import Any

import pytest
import yaml

from ulis.sources.connectors import legislation_gov_uk as conn_mod
from ulis.sources.connectors.legislation_gov_uk import LegislationGovUkConnector
from ulis.sources.connectors.local_manifest import ManifestError

SITE = "https://www.legislation.gov.uk"


def _akn(expression_date: str, events: list[tuple[str, str]] | None = None) -> bytes:
    refs = "".join(
        f'<eventRef {attrs} date="{day}" eId="date-{i}" source="#"/>' for i, (day, attrs) in enumerate(events or [])
    )
    return (
        "<akomaNtoso><meta><identification><FRBRExpression>"
        f'<FRBRdate date="{expression_date}" name="validFrom"/></FRBRExpression></identification>'
        f"<lifecycle>{refs}</lifecycle></meta></akomaNtoso>"
    ).encode()


class _Response:
    def __init__(self, url: str, body: bytes):
        self.url, self.body, self.status = url, body, 200

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body

    def geturl(self) -> str:
        return self.url


def _stub_site(monkeypatch: pytest.MonkeyPatch, routes: dict[str, bytes]) -> list[str]:
    requested: list[str] = []

    def urlopen(request: Any, timeout: float = 0) -> _Response:
        url = request.full_url
        requested.append(url)
        if url not in routes:
            raise urllib.error.HTTPError(url, 404, "stub", Message(), io.BytesIO(b""))
        return _Response(url, routes[url])

    monkeypatch.setattr(conn_mod.urllib.request, "urlopen", urlopen)
    return requested


def _connector(tmp_path: Path, works: list[Any], **top: Any) -> LegislationGovUkConnector:
    manifest = {
        "schema_version": 1,
        "source_id": "gb-legislation-gov-uk",
        "base_url": SITE,
        "user_agent": "ULIS-test (+https://example.org)",
        "crawl_delay_seconds": 1,
        "works": works,
        **top,
    }
    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / "manifest.yaml"
    path.write_text(yaml.safe_dump(manifest))
    return LegislationGovUkConnector(path, "gb-legislation-gov-uk", tmp_path / "cache", sleep=lambda _s: None)


ROBOTS = {f"{SITE}/robots.txt": b"User-agent: *\nCrawl-delay: 5\nDisallow: */data.pdf\n"}


def test_compact_entries_take_their_history_from_the_manifest_default(tmp_path: Path) -> None:
    works = [
        "uksi/2018/480",  # a plain path: history per history_default
        "uksi/2001/5@made",  # one version only: as made
        "ukpga/1990/7@enacted",
        {"path": "ukpga/1998/42", "history": False, "title": "Human Rights Act 1998"},
        {"path": "ukpga/1999/1", "version": "enacted"},
    ]
    normalised = _connector(tmp_path, works, history_default=True)._manifest()["works"]  # noqa: SLF001
    assert [(w["path"], w["history"], w.get("version")) for w in normalised] == [
        ("uksi/2018/480", True, None),
        ("uksi/2001/5", False, "made"),
        ("ukpga/1990/7", False, "enacted"),
        ("ukpga/1998/42", False, None),
        ("ukpga/1999/1", True, "enacted"),  # a version means no history is fetched, whatever the flag
    ]
    # Without history_default the old behaviour stands: a work has no history unless it says so.
    plain = _connector(tmp_path / "b", ["ukpga/1998/42", {"path": "ukpga/1978/30", "history": True}])._manifest()  # noqa: SLF001
    assert [w["history"] for w in plain["works"]] == [False, True]


def test_manifest_refuses_bad_compact_entries_and_versions(tmp_path: Path) -> None:
    for bad, message in (
        ("uksi/2001/5@revised", "invalid work entry"),
        ("uksi/2001/5@", "invalid work entry"),
        ("../uksi/2001/5@made", "invalid work path"),
        ({"history": True}, "invalid work entry"),
        ({"path": "uksi/2001/5", "version": "latest"}, "invalid version"),
        (42, "invalid work entry"),
    ):
        with pytest.raises(ManifestError, match=message):
            _connector(tmp_path, [bad])._manifest()  # noqa: SLF001
    with pytest.raises(ManifestError, match="listed twice"):
        _connector(tmp_path, ["uksi/2001/5@made", {"path": "uksi/2001/5"}])._manifest()  # noqa: SLF001
    # The unusual session forms of the older parliaments are paths of the site.
    for ok in ("aosp/1540/SessMar14/37", "apni/Edw8&1Geo6/1/4", "aep/Edw1/3/1", "nisi/1987/1234", "ukci/2001/1"):
        _connector(tmp_path, [ok])._manifest()  # noqa: SLF001


def test_a_work_in_one_version_is_fetched_from_its_enacted_or_made_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    routes = {
        **ROBOTS,
        f"{SITE}/uksi/2001/5/made/data.akn": _akn("2001-02-03"),
        f"{SITE}/ukpga/1990/7/enacted/data.akn": _akn("1990-05-01"),
    }
    requested = _stub_site(monkeypatch, routes)
    conn = _connector(tmp_path, ["uksi/2001/5@made", "ukpga/1990/7@enacted", "ukpga/1990/8@enacted"])
    docs = list(conn.fetch())
    assert [d.external_id for d in docs] == [
        "legislation.gov.uk:uksi/2001/5@2001-02-03",
        "legislation.gov.uk:ukpga/1990/7@1990-05-01",
    ]
    assert docs[0].metadata["point_in_time"] == "2001-02-03" and docs[0].media_type == "application/akn+xml"
    assert requested == [
        f"{SITE}/robots.txt",
        f"{SITE}/uksi/2001/5/made/data.akn",
        f"{SITE}/ukpga/1990/7/enacted/data.akn",
        f"{SITE}/ukpga/1990/8/enacted/data.akn",
    ]
    assert conn.fetch_errors == ["ukpga/1990/8: enacted version not found (404)"]


def test_history_fetches_every_lifecycle_date_except_the_date_an_instrument_was_made(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = [
        ("2018-04-11", 'refersTo="#made"'),  # no version of the instrument exists on the day it was made
        ("2018-05-25", 'refersTo="#coming-into-force"'),
        ("2018-05-25", ""),
        ("2019-04-01", ""),
        ("2026-09-30", ""),
    ]
    routes = {
        **ROBOTS,
        f"{SITE}/uksi/2018/480/data.akn": _akn("2026-09-30", events),
        f"{SITE}/uksi/2018/480/2018-05-25/data.akn": _akn("2018-05-25"),
        f"{SITE}/uksi/2018/480/2019-04-01/data.akn": _akn("2019-04-01"),
    }
    requested = _stub_site(monkeypatch, routes)
    docs = list(_connector(tmp_path, ["uksi/2018/480"], history_default=True).fetch())
    assert [d.metadata["point_in_time"] for d in docs] == ["2018-05-25", "2019-04-01", "2026-09-30"]  # oldest first
    assert f"{SITE}/uksi/2018/480/2018-04-11/data.akn" not in requested
    assert len(requested) == 4  # robots.txt, the current text and the two earlier versions


def test_a_made_date_that_is_also_a_later_event_is_still_fetched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = [("2020-01-01", 'refersTo="#made"'), ("2020-01-01", ""), ("2021-06-01", "")]
    routes = {
        **ROBOTS,
        f"{SITE}/uksi/2020/1/data.akn": _akn("2021-06-01", events),
        f"{SITE}/uksi/2020/1/2020-01-01/data.akn": _akn("2020-01-01"),
    }
    _stub_site(monkeypatch, routes)
    docs = list(_connector(tmp_path, [{"path": "uksi/2020/1", "history": True}]).fetch())
    assert [d.metadata["point_in_time"] for d in docs] == ["2020-01-01", "2021-06-01"]


def test_a_version_the_site_does_not_have_is_not_asked_for_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    events = [("2010-01-01", ""), ("2012-01-01", ""), ("2015-01-01", "")]
    routes = {
        **ROBOTS,
        f"{SITE}/ukpga/2000/1/data.akn": _akn("2015-01-01", events),
        f"{SITE}/ukpga/2000/1/2012-01-01/data.akn": _akn("2012-01-01"),
        # 2010-01-01: the site answers 404
    }
    requested = _stub_site(monkeypatch, routes)
    works = [{"path": "ukpga/2000/1", "history": True}]
    first = list(_connector(tmp_path, works).fetch())
    assert [d.metadata["point_in_time"] for d in first] == ["2012-01-01", "2015-01-01"]
    n = len(requested)
    again = list(_connector(tmp_path, works).fetch())  # same cache directory: nothing is requested again
    assert len(again) == 2 and len(requested) == n
