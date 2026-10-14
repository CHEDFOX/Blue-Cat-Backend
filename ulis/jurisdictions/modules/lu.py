"""Luxembourg module: citations of consolidated legislation, in French (the language of Luxembourg legislation).

* article: ``Art. 1382 du Code civil``, ``Art. L. 121-1 du Code du travail``, ``Art. 11 de la Constitution ...``
* numbered paragraph: ``Art. 63, paragraphe 2 du Code civil``; the first paragraph is ``paragraphe 1er``
* an act or regulation is cited with its title, which begins with its kind and date:
  ``Art. 5 de la loi du 1er août 2018 portant organisation de la Commission nationale pour la protection des
  données ...``, ``Art. 3 du règlement grand-ducal du ...``

The article label is the one Legilux prints (``1er``, ``L. 010-1``, ``9 et 10``). The title is the one
Legilux publishes (the short title, if it has one, else the title without the "Version consolidée applicable
au ..." prefix); the preposition (de la / du / de l') is chosen from its first word. Luxembourg legal writing
often shortens citations (``C. civ.``, ``art. 1382 CC``) and adds "modifiée"; the long form is used here.
Formatting conventions only; to be confirmed by a Luxembourg reviewer before the LU profile is "reviewed".
"""

from __future__ import annotations

import re
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from ulis.jurisdictions.modules.base import JurisdictionModule, ProvisionCitationInput

# First word of a title -> the words that join an article to it ("Art. 5" + "de la loi du ...").
PREPOSITIONS = {
    "loi": "de la",
    "constitution": "de la",
    "convention": "de la",
    "décision": "de la",
    "règlement": "du",
    "code": "du",
    "nouveau": "du",
    "arrêté": "de l'",
    "ordonnance": "de l'",
    "accord": "de l'",
    "acte": "de l'",
}
KEEP_CAPITAL = {"code", "nouveau", "constitution"}


class LuExt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # ELI of the consolidated work, e.g. http://data.legilux.public.lu/eli/etat/leg/code/civil
    eli: str = Field(pattern=r"^http://data\.legilux\.public\.lu/eli/etat/leg/[a-z]{1,12}(?:/[A-Za-z0-9_.-]+)+$")
    # Kind of text in the ELI: code, constitution, loi, rgd (règlement grand-ducal), rmin, agd, amin...
    act_type: str = Field(pattern=r"^[a-z]{1,12}$")
    # The act the consolidation belongs to (jolux:isMemberOf), e.g. the law of 21 March 1804 for the Code civil.
    base_act: str | None = Field(default=None, pattern=r"^http://data\.legilux\.public\.lu/eli/etat/leg/.+$")
    entry_into_force: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    # The jolux:dateApplicability stated inside the file when it differs from the date of the consolidation (the
    # ELI date): the file then carries another consolidation's metadata.
    metadata_date: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


def paragraph_label(key: str) -> str:
    return "1er" if key == "1" else key


def with_preposition(title: str) -> str:
    """``Loi du 1er août 2018 ...`` -> ``de la loi du 1er août 2018 ...``; ``Code civil`` -> ``du Code civil``."""
    first = re.split(r"[\s']", title.strip(), maxsplit=1)[0].lower()
    joint = PREPOSITIONS.get(first)
    if joint is None:
        return f"de {title}"
    body = title if first in KEEP_CAPITAL else title[:1].lower() + title[1:]
    return f"{joint}{body}" if joint.endswith("'") else f"{joint} {body}"


class LuModule(JurisdictionModule):
    id: ClassVar[str] = "lu"
    version: ClassVar[str] = "1.0.0"
    ext_model: ClassVar[type[BaseModel]] = LuExt

    def pinpoint(self, c: ProvisionCitationInput) -> str:
        if not c.num_path:
            return ""
        out = f"Art. {c.num_path[0]}"
        for part in c.num_path[1:]:
            kind, _, label = part.partition(":")
            if kind == "para" and label:
                out += f", paragraphe {paragraph_label(label)}"
        return out

    def provision_citation(self, c: ProvisionCitationInput) -> str:
        pin = self.pinpoint(c)
        return f"{pin} {with_preposition(c.instrument_title)}".strip() if pin else c.instrument_title
