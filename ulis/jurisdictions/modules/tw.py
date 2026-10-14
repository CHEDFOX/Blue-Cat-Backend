"""Taiwan module: citations of laws of the Republic of China in Taiwanese convention.

* ``民法第184條``, ``民法第184條第1項`` (article, paragraph)
* ``個人資料保護法第6條第1項第2款``; items of a single-paragraph article ``個人資料保護法第2條第1款``
* sub-items ``勞動基準法第59條第4款第1目``; inserted articles ``個人資料保護法第21條之1``
* ``刑法第271條第1項``, ``憲法第8條``, ``憲法增修條文第2條第1項``, ``憲法前言``

Numbers are written in Arabic numerals and 第1項 is omitted for an article with a single
paragraph, as in court decisions. The Criminal Code (中華民國刑法), the Constitution
(中華民國憲法) and its Additional Articles are cited without 中華民國, the form used in court
decisions; every other law by its full title. Formatting conventions only; to be confirmed by a
Taiwanese reviewer before the TW profile is "reviewed".
"""

from __future__ import annotations

import re
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from ulis.jurisdictions.modules.base import JurisdictionModule, ProvisionCitationInput

UNITS = {"article": "條", "paragraph": "項", "item": "款", "subitem": "目"}
CONVENTIONAL_TITLES = {
    "中華民國刑法": "刑法",
    "中華民國憲法": "憲法",
    "中華民國憲法增修條文": "憲法增修條文",
}
_TOKEN = re.compile(r"(article|paragraph|item|subitem)-([0-9]+(?:_[0-9]+)?)")


class TwExt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pcode: str  # 全國法規資料庫 law code, e.g. B0000001
    category: str | None = None  # 法規類別, e.g. 行政 ＞ 法務部 ＞ 法律事務目
    amended_on: str | None = None  # 修正日期 (latest amendment promulgated), ISO date


def number(value: str, unit: str) -> str:
    """``184`` -> 第184條; ``21_1`` -> 第21條之1 (an inserted article)."""
    head, *branches = value.split("_")
    return f"第{head}{unit}" + "".join(f"之{b}" for b in branches)


def citation_title(title: str) -> str:
    return CONVENTIONAL_TITLES.get(title, title)


class TwModule(JurisdictionModule):
    id: ClassVar[str] = "tw"
    version: ClassVar[str] = "1.0.0"
    ext_model: ClassVar[type[BaseModel]] = TwExt

    def pinpoint(self, c: ProvisionCitationInput) -> str:
        if c.eid == "preamble":
            return "前言"
        return "".join(number(value, UNITS[kind]) for kind, value in _TOKEN.findall(c.eid))

    def provision_citation(self, c: ProvisionCitationInput) -> str:
        return f"{citation_title(c.instrument_title)}{self.pinpoint(c)}"
