"""Shared base of the United States state-law modules (Washington, Texas, Oregon, Utah).

A state's statutes are cited by the code and the section number alone, not by the title of an enactment, so a
state module only supplies the code's citation form (``RCW``, ``Tex. Penal Code``, ``ORS``, ``Utah Code``) and the
sign before the section number (``§``, ``art.`` or none). Subdivisions of a section are written in parentheses
after it (``RCW 9A.32.030(1)(a)``). They are formatting conventions for research output, reviewed by no one yet.
"""

from __future__ import annotations

from ulis.jurisdictions.modules.base import JurisdictionModule, ProvisionCitationInput, subdivision


class UsStateModule(JurisdictionModule):
    def code_citation(self, c: ProvisionCitationInput) -> str:
        """The code's citation form, e.g. ``RCW`` or ``Tex. Penal Code``."""
        raise NotImplementedError

    def unit_sign(self, c: ProvisionCitationInput) -> str:
        """What stands between the code and the number: ``§``, ``art.`` or nothing."""
        return "§"

    def pinpoint(self, c: ProvisionCitationInput) -> str:
        parts = [p for p in c.num_path if p]
        if not parts:
            return ""
        sign = self.unit_sign(c)
        return (f"{sign} " if sign else "") + parts[0] + "".join(subdivision(p) for p in parts[1:])

    def provision_citation(self, c: ProvisionCitationInput) -> str:
        pin = self.pinpoint(c)
        return f"{self.code_citation(c)} {pin}".strip() if pin else self.code_citation(c)
