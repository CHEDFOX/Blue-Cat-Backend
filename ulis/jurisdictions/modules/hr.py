"""Croatia module: citation conventions for acts published in Narodne novine.

* ``članak 5. stavak 2. točka 3. Zakona o službenoj iskaznici (NN 30/2023)`` - article (članak), paragraph (stavak),
  point (točka) and sub-point (podtočka, ``podtočka a)``), then the act's name in the genitive and the gazette reference
  ``NN <edition>/<year>``; an inserted article is written ``članak 12.a``;
* the name is put in the genitive for the usual forms (``Zakon o ...`` -> ``Zakona o ...``, ``Ustav ...`` -> ``Ustava ...``,
  ``Ustavni zakon`` -> ``Ustavnog zakona``, ``Kazneni zakon`` -> ``Kaznenog zakona``); any other title is cited as
  ``<title>, članak 5. (NN 30/2023)``;
* a consolidated text (``... (pročišćeni tekst)``) is cited ``Zakona o radu (pročišćeni tekst, NN 137/2004)``;
* units outside the articles: ``uvodni dio`` (promulgation decree, title, preamble), ``završni dio`` (signature),
  ``prilog 1.`` (annex).

Formatting conventions only; to be confirmed by a Croatian reviewer before the HR profile is "reviewed".
"""

from __future__ import annotations

import re
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from ulis.jurisdictions.modules.base import JurisdictionModule, ProvisionCitationInput


class HrExt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    eli: str  # "sluzbeni/2023/30/510" - the ELI path below https://narodne-novine.nn.hr/eli/
    nn_year: int
    nn_edition: int  # the number of the edition of Narodne novine
    nn_number: int  # the number of the act in that edition
    nn_citation: str  # "NN 30/2023"
    document_type: str | None = None  # the ELI document type (ZAKON, USTAV ...), when the page carries ELI metadata
    consolidated_text: bool = False  # a "pročišćeni tekst" published in the gazette
    amends: list[str] = Field(default_factory=list)  # ELI paths of acts this act amends
    repeals: list[str] = Field(default_factory=list)  # ELI paths of acts this act repeals


def _number(key: str) -> str:
    """``12`` -> ``12.``; ``12a`` -> ``12.a``"""
    m = re.fullmatch(r"(\d+)([a-z]*)", key)
    if not m:
        return key
    return f"{m.group(1)}.{m.group(2)}" if m.group(2) else f"{m.group(1)}."


def genitive(title: str) -> str | None:
    """The act's name in the genitive when it has a regular form, else ``None``."""
    if m := re.match(r"^Ustavni zakon\b(.*)$", title):
        return "Ustavnog zakona" + m.group(1)
    for nominative, gen in (("Zakon", "Zakona"), ("Zakonik", "Zakonika"), ("Ustav", "Ustava")):
        if re.match(rf"^{nominative}\b", title):
            return gen + title[len(nominative) :]
    if m := re.match(r"^([A-ZŠĐČĆŽ][a-zšđčćž]+(?:n|k|j)i) zakon\b(.*)$", title):
        return m.group(1)[:-1] + "og zakona" + m.group(2)
    return None


class HrModule(JurisdictionModule):
    id: ClassVar[str] = "hr"
    version: ClassVar[str] = "1.0.0"
    ext_model: ClassVar[type[BaseModel]] = HrExt

    def pinpoint(self, c: ProvisionCitationInput) -> str:
        parts = [p for p in c.num_path if p]
        if not parts:
            return ""
        out: list[str] = []
        for i, part in enumerate(parts):
            tag, _, key = part.partition(":")
            if i == 0 and part == "preamble":
                out.append("uvodni dio")
            elif i == 0 and tag == "zav":
                out.append("završni dio")
            elif i == 0 and tag == "pril":
                out.append(f"prilog {key}.")
            elif tag == "art":
                out.append(f"članak {_number(key)}")
            elif tag == "par":
                out.append(f"stavak {_number(key)}")
            elif tag == "pt":
                out.append(f"točka {_number(key)}")
            elif tag == "sub":
                out.append(f"podtočka {key})")
            else:
                out.append(part)
        return " ".join(out)

    def provision_citation(self, c: ProvisionCitationInput) -> str:
        pin = self.pinpoint(c)
        nn = c.ext.get("nn_citation")
        title = c.instrument_title
        consolidated = bool(c.ext.get("consolidated_text"))
        if consolidated:
            title = re.sub(r"\s*\(pro[cč]i[sš]?ćeni tekst\)\s*$", "", title, flags=re.IGNORECASE)
        reference = f"(pročišćeni tekst, {nn})" if consolidated and nn else (f"({nn})" if nn else "")
        name = genitive(title)
        if name is None:
            return " ".join(x for x in (f"{title},", pin, reference) if x)
        return " ".join(x for x in (pin, name, reference) if x)
