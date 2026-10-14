"""Poland module: Polish citations of acts from the Dziennik Ustaw (Polish only).

One documented style, the pinpoint from the largest unit to the smallest, then the act's customary
abbreviation or, for an act without one, its name in the genitive (the form used in Polish legal writing):

* ``art. 415 k.c.``; ``art. 148 § 1 k.k.``; ``art. 22 § 1 k.p.a.``
* an act that divides its articles into ustępy: ``art. 6 ust. 1 pkt 2 lit. a ustawy o ochronie danych osobowych``
* points (pkt) and letters (lit.) follow the paragraph (§) or ustęp (ust.): ``art. 148 § 2 pkt 1 k.k.``
* an article inserted between two others carries a superscript, and so does an inserted paragraph, point or
  letter: ``art. 22¹ k.c.``, ``art. 18³ᵃ k.p.``; a range of repealed articles: ``art. 109-157``

The abbreviation (``k.c.``, ``k.k.``, ``k.p.``, ``k.p.a.``, ``k.r.o.``) or genitive name comes from the
parser's table of the ingested acts (``ext.cite_as``); for an act without one the citation is
``art. 415 (Title)``. Formatting conventions only; to be confirmed by a Polish reviewer before the PL
profile is "reviewed". The formal reference to the official publication (``Dz.U. 2024 poz. 1061``) is not
generated; the address of the act is kept in ``ext.dz_u``.
"""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from ulis.jurisdictions.modules.base import JurisdictionModule, ProvisionCitationInput

SUPER_DIGITS = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")
SUPER_LETTERS = dict(zip("abcdefghijklmnoprstuvwxyz", "ᵃᵇᶜᵈᵉᶠᵍʰⁱʲᵏˡᵐⁿᵒᵖʳˢᵗᵘᵛʷˣʸᶻ", strict=True))
UNIT_WORDS = {"art": "art.", "par": "§", "ust": "ust.", "pkt": "pkt", "lit": "lit."}


class PlExt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dz_u: str  # the act's address in the Dziennik Ustaw, "Dz.U. 1964 nr 16 poz. 93"
    eli: str  # the act's ELI address, https://eli.gov.pl/eli/DU/1964/93/ogl
    short_name: str  # "Kodeks cywilny"
    cite_as: str | None = None  # "k.c." or "ustawy o ochronie danych osobowych", for citations
    notes: list[str] | None = None  # the publisher's footnotes on the act's title (transposed directives ...)


def superscript(text: str) -> str:
    """``3a`` -> ``³ᵃ``; a character without a superscript form makes it ``(3q)`` instead."""
    out: list[str] = []
    for ch in text:
        if ch.isascii() and ch.isdigit():
            out.append(ch.translate(SUPER_DIGITS))
        elif ch in SUPER_LETTERS:
            out.append(SUPER_LETTERS[ch])
        else:
            return f"({text})"
    return "".join(out)


def number(label: str) -> str:
    """``22_1`` -> ``22¹``; ``18_3a`` -> ``18³ᵃ``; ``5a`` -> ``5a``; ``109-157`` -> ``109-157``."""
    base, _, sup = label.partition("_")
    return base + (superscript(sup) if sup else "")


class PlModule(JurisdictionModule):
    id: ClassVar[str] = "pl"
    version: ClassVar[str] = "1.0.0"
    ext_model: ClassVar[type[BaseModel]] = PlExt

    def pinpoint(self, c: ProvisionCitationInput) -> str:
        out: list[str] = []
        for part in c.num_path:
            kind, _, label = part.partition(":")
            if kind in UNIT_WORDS:
                out.append(f"{UNIT_WORDS[kind]} {number(label)}")
            elif kind == "div":
                out.append(label)
            elif kind == "preamble":
                out.append("preambuła")
        return " ".join(out)

    def provision_citation(self, c: ProvisionCitationInput) -> str:
        pin = self.pinpoint(c)
        cite_as = c.ext.get("cite_as")
        if cite_as:
            return f"{pin} {cite_as}".strip()
        return f"{pin} ({c.instrument_title})" if pin else c.instrument_title
