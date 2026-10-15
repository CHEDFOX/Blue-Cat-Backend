"""Generic module for jurisdictions of which only the constitution is loaded (``citation.module: constitution``).

A constitution is cited by the country's name and the number the document itself gives to the provision:

* ``Ghana Constitution art. 12``, ``Ghana Constitution art. 12(3)`` (an article and its numbered paragraph),
* ``Nigeria Constitution s. 33`` for documents that number *sections*,
* ``China Constitution`` in Chinese: ``中华人民共和国宪法第51条`` (title + 第N条), paragraph ``第2款``; in
  traditional characters (``zh-Hant``) ``澳門基本法第23條``,
* ``Angola Constitution art. 107-A`` for an article added with a letter (the document's ``107.º-A``),
* ``Angola Constitution, preamble`` and ``Angola Constitution, text block 3`` (a document whose structure was not
  recognised is stored in blocks; a block has no number of its own in the constitution).

The title is the source's short title, which the manifest sets to "<Country> Constitution" (a label, not a
quotation). Formatting convention only; the module claims no national citation style and is not a statement of how
that country's courts cite its constitution.
"""

from __future__ import annotations

import re
from typing import ClassVar

from ulis.jurisdictions.modules.base import JurisdictionModule, ProvisionCitationInput

_EID = re.compile(
    r"^(?P<kind>art|sec)-(?P<num>[0-9]+)(?P<letters>[a-z]*)(?:_(?P<branch>[0-9]+))?(?:-par(?P<par>[0-9]+[a-z]?))?$"
)
_BLOCK = re.compile(r"^blk-(?P<n>[0-9]+)$")
NO_SPACE = {"zh", "zh-Hant"}  # languages whose citations join title and pinpoint without a space
ARTICLE_MARK = {"zh": "条", "zh-Hant": "條"}  # simplified and traditional character of 第N条 / 第N條


def _number(m: re.Match[str]) -> str:
    return m.group("num") + m.group("letters").upper() + (f"-{m.group('branch')}" if m.group("branch") else "")


class ConstitutionModule(JurisdictionModule):
    id: ClassVar[str] = "constitution"
    version: ClassVar[str] = "1.0.0"

    def pinpoint(self, c: ProvisionCitationInput) -> str:
        if c.eid == "preamble":
            return "preamble"
        if b := _BLOCK.match(c.eid):
            return f"text block {b.group('n')}"
        m = _EID.match(c.eid)
        if not m:
            return super().pinpoint(c)
        number = _number(m)
        if c.language in ARTICLE_MARK:
            return f"第{number}{ARTICLE_MARK[c.language]}" + (f"第{m.group('par')}款" if m.group("par") else "")
        label = "s." if m.group("kind") == "sec" else "art."
        return f"{label} {number}" + (f"({m.group('par')})" if m.group("par") else "")

    def provision_citation(self, c: ProvisionCitationInput) -> str:
        pin = self.pinpoint(c)
        if c.eid == "preamble" or _BLOCK.match(c.eid):
            return f"{c.instrument_title}, {pin}"
        if c.language in NO_SPACE:
            return f"{c.instrument_title}{pin}"
        return f"{c.instrument_title} {pin}"
