"""The scripts that make the committed manifests from the publishers' own lists (SE, DK, NL, IE).

They are not part of the package, so they are loaded from ``scripts/``. Only their reading of a list is tested here
(small excerpts of what the publishers serve); the requests they make are the connectors' ``PoliteHttp``.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from ulis.settings import REPO_ROOT


def _script(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sfs_list_records_are_told_apart() -> None:
    sfs = _script("build_sfs_manifest")

    def record(**kw: object) -> dict[str, object]:
        base = {"id": "sfs-2008-1300", "doktyp": "sfs", "subtyp": "sfst", "beteckning": "2008:1300", "titel": "T  (x)"}
        return {**base, "sokdata": {"statusrad": "<dl><dt>Utfärdad</dt> <dd> 2008-12-04 </dd></dl>"}, **kw}

    assert sfs.classify(record()) == ("work", "2008:1300", "T (x)")
    gone = record(sokdata={"statusrad": "<dl><dt>Författningen är upphävd</dt></dl>"})
    assert sfs.classify(gone) == ("repealed", "sfs-2008-1300", "")
    assert sfs.classify(record(id="sfs-n2026-3", beteckning="N2026:3")) == ("other", "sfs-n2026-3", "")
    assert sfs.classify(record(id="sfs-1905-38 s.1", beteckning="1905:38 s.1"))[0] == "other"
    assert sfs.classify(record(id="d6s0riksb", subtyp="regl-riksb")) == ("foreign", "d6s0riksb", "")
    # an id with a leading zero is kept as printed: sfs-1810-0926 is not sfs-1810-926
    assert sfs.classify(record(id="sfs-1810-0926", beteckning="1810:0926"))[1] == "1810:0926"
    assert sfs.FIRST[0] == "1974:152"


def test_irish_year_lists_in_both_layouts_of_the_table() -> None:
    ie = _script("build_ie_manifest")
    compact = (
        '<tbody><tr><td class="align-center">1</td><td><a href="en/act/pub/0001/index.html">Fisheries (Commissions) '
        'Act, 1997</a></td><td class="align-center"><a href="../pdf/1997/en.act.1997.0001.pdf">PDF</a></td></tr>'
        '<tr><td class="align-center">2</td><td><a href="en/act/pub/0002/index.html">European Parliament Elections '
        "Act, 1997</a></td></tr>"
    )
    spaced = (
        '<tbody>\n\t<tr>\n\t    <td class="align-center">1</td>\n\t    <td>\n'
        '\t        <a href="en/act/pub/0001/index.html">Ministers and Secretaries (Amendment) Act 2025</a>\n'
        "\t    </td>\n\t</tr>"
    )
    assert ie.titles(compact) == [
        (1, "Fisheries (Commissions) Act, 1997"),
        (2, "European Parliament Elections Act, 1997"),
    ]
    assert ie.titles(spaced) == [(1, "Ministers and Secretaries (Amendment) Act 2025")]
    # a Constitutional Amendment Act (en/act/cam/...) is not a Public Act
    assert ie.titles('<tr><td><a href="en/act/cam/0017/index.html">Seventeenth Amendment</a></td></tr>') == []
    with pytest.raises(SystemExit, match="links to Act 0003"):
        ie.titles('<tr><td class="align-center">2</td><td><a href="en/act/pub/0003/index.html">X</a></td></tr>')
    assert [(y, n) for y, n, _, _ in ie.PILOT] == [
        (1977, 10),
        (1997, 26),
        (2001, 50),
        (2007, 19),
        (2014, 38),
        (2018, 7),
    ]


def test_danish_sitemap_and_eli_metadata() -> None:
    dk = _script("build_dk_manifest")
    sitemap = (
        "<urlset><url><loc>https://retsinformation.dk/eli/retsinfo/2026/10167</loc><lastmod>2026-10-14</lastmod></url>"
        "<url><loc>https://retsinformation.dk/eli/lta/2026/888</loc></url>"
        "<url><loc>https://www.retsinformation.dk/eli/lta/2025/1294</loc></url>"
        "<url><loc>https://retsinformation.dk/eli/ft/20261BB00030</loc></url></urlset>"
    )
    assert sorted((int(y), int(n)) for y, n in dk._LTA.findall(sitemap)) == [(2025, 1294), (2026, 888)]
    eli = "http://data.europa.eu/eli/ontology#"
    auth = "http://www.retsinformation.dk/eli/resource/authority/type_document#"
    nodes = [
        {
            "@id": "https://retsinformation.dk/eli/lta/2025/1294",
            eli + "type_document": [{"@id": auth + "LBKH"}],
            eli + "in_force": [{"@id": eli + "InForce-inForce"}],
            eli + "date_document": [{"@value": "07-11-2025 00:00:00"}],
        },
        {
            "@id": "https://retsinformation.dk/eli/lta/2025/1294/dan",
            eli + "title": [{"@value": "Bekendtgørelse af straffeloven"}],
            eli + "title_alternative": [{"@value": "Straffeloven"}],
        },
    ]
    row = dk.classify(json.dumps(nodes).encode(), 2025, 1294)
    assert row == {
        "type": "LBKH",
        "in_force": "inForce",
        "date": "2025-11-07",
        "alt": "Straffeloven",
        "title": "Bekendtgørelse af straffeloven",
    }
    old = json.loads(json.dumps(nodes))
    old[0][eli + "in_force"] = [{"@id": eli + "InForce-notInForce"}]  # a superseded consolidation
    assert dk.classify(json.dumps(old).encode(), 2025, 1294)["in_force"] == "notInForce"
    with pytest.raises(ValueError):
        dk.classify(b"<!doctype html><html></html>", 2025, 1294)  # the website's shell, not metadata
    assert "lta/2014/433" in dk.PILOT and dk.PILOT.count("consolidations:") == 6


def test_dutch_sitemap_lists_regulations_and_treaties() -> None:
    bwb = _script("build_bwb_manifest")
    sitemap = (
        "<urlset><url><loc>https://wetten.overheid.nl/BWBR0001821/</loc><lastmod>2016-01-12</lastmod></url>"
        "<url><loc>https://wetten.overheid.nl/BWBV0001234/</loc></url>"
        "<url><loc>https://wetten.overheid.nl/BWBW1234/</loc></url></urlset>"
    )
    assert bwb._LOC.findall(sitemap) == ["BWBR0001821", "BWBV0001234"]  # BWBW has a different number of digits
    assert bwb.HOST == "wetten.overheid.nl" and bwb.SITEMAP == "https://wetten.overheid.nl/sitemap.xml"
    assert [p for p in __import__("re").findall(r"bwb_id: (BWBR\d{7})", bwb.PILOT)][:2] == [
        "BWBR0001840",
        "BWBR0040940",
    ]


def test_every_script_identifies_itself_and_stays_on_its_publishers_host() -> None:
    for name, host in (
        ("build_sfs_manifest", "data.riksdagen.se"),
        ("build_dk_manifest", "www.retsinformation.dk"),
        ("build_bwb_manifest", "wetten.overheid.nl"),
        ("build_ie_manifest", "www.irishstatutebook.ie"),
    ):
        module = _script(name)
        assert module.USER_AGENT == "ULIS-research-bot/0.1 (+https://github.com/CHEDFOX/Blue-Cat-Backend)"
        assert host == module.HOST
        assert isinstance(module.OUT, Path) and module.OUT.name.endswith(".yaml")
