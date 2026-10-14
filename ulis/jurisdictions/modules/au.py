"""Australia module: AGLC-style citations for Commonwealth legislation (Federal Register of Legislation).

* Acts: ``Privacy Act 1988 (Cth) s 6(1)``; definitions ``Privacy Act 1988 (Cth) s 6(1) (definition of
  ‘agency’ para (a))``; schedules ``Privacy Act 1988 (Cth) sch 1 cl 1.2(a)`` (the Australian Privacy
  Principles number their subclauses ``1.2``, which replace the clause number);
* a schedule enacted as a code: ``Criminal Code (Cth) s 11.1(1)``, ``Criminal Code (Cth) Dictionary
  (definition of ‘ABN’)``;
* the Constitution: ``Australian Constitution s 51(xx)`` (the source prints ``(xx.)``); the covering
  clauses of the Imperial Act: ``Commonwealth of Australia Constitution Act 1900 (Imp) s 5``.

Section numbers and labels are the source's own. Formatting conventions only; to be confirmed by
an Australian reviewer before the AU profile is "reviewed" (see docs/jurisdictions/au.md).
"""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import BaseModel, ConfigDict

from ulis.jurisdictions.modules.base import JurisdictionModule, ProvisionCitationInput

CONSTITUTION = "Australian Constitution"
# The Register lists the Commonwealth of Australia Constitution Act (title C2004Q00685) with the
# making date 9 July 1900; it is an Act of the United Kingdom Parliament (Imp).
CONSTITUTION_ACT = "Commonwealth of Australia Constitution Act 1900 (Imp)"


class AuExt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title_id: str  # Register title id, e.g. C2004A03712
    collection: Literal["Act", "Constitution"]
    act_number: str | None = None  # as printed on the compilation, e.g. "No. 119, 1988"
    code_title: str | None = None  # a schedule enacted as a code (e.g. "Criminal Code")


def _subs(parts: list[str]) -> str:
    """``(1)``, ``(a)``, ``(xx.)`` -> ``(1)(a)(xx)``; definitions -> `` (definition of ‘x’ para (a))``."""
    out = ""
    definition: str | None = None
    after: list[str] = []
    for part in parts:
        if part.startswith("def:"):
            definition = part[4:]
            after = []
            continue
        label = part.strip()
        if label.startswith("(") and label.endswith(".)"):
            label = label[:-2] + ")"  # "(xx.)" -> "(xx)"
        if not label.startswith("("):
            label = f"({label})"
        if definition is None:
            out += label
        else:
            after.append(label)
    if definition is not None:
        out += f" (definition of ‘{definition}’" + (f" para {''.join(after)}" if after else "") + ")"
    return out


class AuModule(JurisdictionModule):
    id: ClassVar[str] = "au"
    version: ClassVar[str] = "1.0.0"
    ext_model: ClassVar[type[BaseModel]] = AuExt

    def _title(self, c: ProvisionCitationInput) -> str:
        if c.ext.get("collection") == "Constitution":
            return CONSTITUTION if c.top_kind in ("section", "schedule") else CONSTITUTION_ACT
        if c.top_kind in ("code_section", "code_dictionary") and c.ext.get("code_title"):
            return f"{c.ext['code_title']} (Cth)"
        return f"{c.instrument_title} (Cth)"

    def pinpoint(self, c: ProvisionCitationInput) -> str:
        parts = [p for p in c.num_path]
        kind = c.top_kind
        if kind == "preamble":
            return "Preamble"
        if kind == "long_title":
            return "long title"
        if kind in ("dictionary", "code_dictionary"):
            return "Dictionary" + _subs(parts)
        if kind == "schedule":
            schedule = parts[0] if parts else ""
            head = f"sch {schedule}" if schedule else "sch"
            rest = parts[1:]
            if not rest:
                return head
            if rest[0].startswith("heading:"):
                return f"{head}, ‘{rest[0][8:]}’"
            clause, subs = rest[0], rest[1:]
            if subs and subs[0][:1].isdigit() and "." in subs[0]:  # APP subclause "1.2" replaces the clause
                clause, subs = subs[0], subs[1:]
            return f"{head} cl {clause}" + _subs(subs)
        if not parts:
            return ""
        if parts[0].startswith("heading:"):
            return f"‘{parts[0][8:]}’"
        return f"s {parts[0]}" + _subs(parts[1:])

    def provision_citation(self, c: ProvisionCitationInput) -> str:
        title = self._title(c)
        pin = self.pinpoint(c)
        if pin.startswith("‘"):
            return f"{title}, {pin}"
        return f"{title} {pin}" if pin else title
