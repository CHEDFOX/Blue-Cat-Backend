"""Ireland module: citations of Acts of the Oireachtas in the Irish convention, in English.

* ``Data Protection Act 2018, s 60(3)(a)``
* ``Data Protection Act 2018, s 2(1) (definition of “controller”)``
* ``Data Protection Act 2018, Sch 1``; a Part of a Schedule: ``Companies Act 2014, Sch 2, Pt 1``
* ``Criminal Justice (Theft and Fraud Offences) Act 2001, long title``

The title is the Act's own short title (the comma the older Acts print before the year is dropped, as
the Irish Statute Book does in its listings). Formatting conventions only; to be confirmed by an Irish
reviewer before the IE profile is "reviewed". Acts are cited by title and year, not by Act number; the
number and year are kept in ``ext`` and in the work URI (``/ie/act/<year>/<number>``).
"""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict

from ulis.jurisdictions.modules.base import JurisdictionModule, ProvisionCitationInput


class IeExt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    act_year: int
    act_number: int
    # How the Act itself provides for commencement (the as-enacted text carries no commencement dates).
    commencement_basis: Literal["by_order", "relative_to_passing", "by_order_and_passing", "unstated"] | None = None
    short_title_as_enacted: str | None = None


def _subs(parts: list[str]) -> str:
    """``(1)``, ``(a)``, ``(i)`` ... are printed without spaces: ``(1)(a)(i)``; a definition is named last."""
    out = ""
    for part in parts:
        if part.startswith("def:"):
            out += f" (definition of “{part[4:]}”)"
        else:
            out += part
    return out


class IeModule(JurisdictionModule):
    id: ClassVar[str] = "ie"
    version: ClassVar[str] = "1.1.0"
    ext_model: ClassVar[type[BaseModel]] = IeExt

    def pinpoint(self, c: ProvisionCitationInput) -> str:
        if c.top_kind == "long_title":
            return "long title"
        if c.top_kind == "schedule":
            # The Schedule of an Act that has only one is printed without a number: "Sch".
            head = f"Sch {c.num_path[0]}" if c.num_path and c.num_path[0] else "Sch"
            rest = [p for p in c.num_path[1:] if p]
            if rest and rest[0].startswith("part:"):
                head += f", Pt {rest[0][5:]}"
                rest = rest[1:]
            if rest and rest[0].startswith("chapter:"):
                head += f", Ch {rest[0][8:]}"
                rest = rest[1:]
            return head + _subs(rest)
        parts = [p for p in c.num_path if p]
        return f"s {parts[0]}" + _subs(parts[1:]) if parts else ""

    def provision_citation(self, c: ProvisionCitationInput) -> str:
        pin = self.pinpoint(c)
        return f"{c.instrument_title}, {pin}" if pin else c.instrument_title
