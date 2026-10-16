"""Japan module: citations of Japanese statutes in Japanese convention.

* ``民法第709条``, ``刑法第199条``
* ``個人情報の保護に関する法律第27条第1項第2号`` (article, paragraph, item)
* ``会社法第2条第3号の2``; sub-items as printed: ``…第2号イ``
* ``刑法第27条の2`` (第二十七条の二), ranges of deleted articles ``民法第38条から第84条まで``
* original supplementary provisions ``民事訴訟法附則第1条``; ``日本国憲法前文``; ``労働基準法別表第一``

Numbers are written in Arabic numerals (horizontal writing), the law's full title is used
throughout and 第1項 is omitted for an article with a single paragraph (its items are cited
``第N条第M号``), following common practice. Formatting conventions only; to be confirmed by a
Japanese reviewer before the JP profile is "reviewed".
"""

from __future__ import annotations

import re
from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict

from ulis.jurisdictions.modules.base import JurisdictionModule, ProvisionCitationInput

UNITS = {"article": "条", "paragraph": "項", "item": "号"}
_TOKEN = re.compile(r"(article|paragraph|item|subitem\d+)-([0-9_:]+)")


class JpExt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    law_id: str  # e-Gov 法令ID, e.g. 129AC0000000089
    law_num: str  # 法令番号, e.g. 明治二十九年法律第八十九号
    law_type: Literal[
        "Constitution",
        "Act",
        "CabinetOrder",
        "ImperialOrder",
        "MinisterialOrdinance",
        "Rule",
        "Misc",
        "Act,ImperialOrder",
        "Act,CabinetOrder",
        "Act,MinisterialOrdinance",
    ]
    abbrev: str | None = None  # e-Gov 法令略称 (comma-separated)


def number(value: str, unit: str) -> str:
    """``27_2`` -> 第27条の2; ``38:84`` -> 第38条から第84条まで; ``2:3`` -> 第2号及び第3号."""
    if ":" in value:
        first, _, last = value.partition(":")
        adjacent = first.isdigit() and last.isdigit() and int(last) == int(first) + 1
        joined = f"{number(first, unit)}及び{number(last, unit)}" if adjacent else None
        return joined or f"{number(first, unit)}から{number(last, unit)}まで"
    head, *branches = value.split("_")
    return f"第{head}{unit}" + "".join(f"の{b}" for b in branches)


class JpModule(JurisdictionModule):
    id: ClassVar[str] = "jp"
    version: ClassVar[str] = "1.0.0"
    ext_model: ClassVar[type[BaseModel]] = JpExt

    def pinpoint(self, c: ProvisionCitationInput) -> str:
        eid = c.eid
        if eid == "preamble":
            return "前文"
        if eid.startswith("appdx-table-"):
            return c.num_path[0] if c.num_path else "別表"
        prefix = "附則" if eid.startswith("suppl-") else ""
        out = prefix
        for i, (kind, value) in enumerate(_TOKEN.findall(eid.removeprefix("suppl-"))):
            if kind in UNITS:
                out += number(value, UNITS[kind])
            else:  # sub-items are cited by their printed title (イ, （１）, ...)
                out += c.num_path[i] if i < len(c.num_path) else value
        return out

    def provision_citation(self, c: ProvisionCitationInput) -> str:
        return f"{c.instrument_title}{self.pinpoint(c)}"
