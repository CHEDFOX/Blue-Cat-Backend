"""Denmark module: Danish citations of consolidated Acts (lovbekendtgørelser from Retsinformation).

One documented style, pinpoint first, then "i" and the law's popular title as used in running
text (a one-word title ending in "-loven" is written in lower case):

* ``§ 276 i straffeloven``; ``§ 3, stk. 2, nr. 1, i forvaltningsloven``
* lettered items: ``§ 81, nr. 1, litra a, i straffeloven``; a range of repealed paragraphs:
  ``§§ 28-30 i straffeloven``
* a commencement provision of an amending act reproduced in the consolidation:
  ``§ 3 i lov nr. 638 af 12. juni 2013``

The genitive form (``straffelovens § 276``) and the formal reference to the consolidation
(``jf. lovbekendtgørelse nr. 1294 af 7. november 2025``) are not generated; the consolidation is
identified by the expression (``dan@lbk-2025-1294``) and its ELI. Formatting conventions only; to
be confirmed by a Danish reviewer before the DK profile is "reviewed".
"""

from __future__ import annotations

import re
from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from ulis.jurisdictions.modules.base import JurisdictionModule, ProvisionCitationInput

POPULAR = re.compile(r"^[A-ZÆØÅ][a-zæøå]+loven$")


class DkExt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    popular_title: str  # Retsinformation PopularTitle, e.g. "Forvaltningsloven"
    document_type: str  # Retsinformation document type of the consolidations, "LBK H"
    title_derived: bool = (
        False  # no PopularTitle: popular_title is the consolidation's title without "Bekendtgørelse af"
    )


def law_name(title: str) -> str:
    return title[:1].lower() + title[1:] if POPULAR.match(title) else title


class DkModule(JurisdictionModule):
    id: ClassVar[str] = "dk"
    version: ClassVar[str] = "1.1.0"
    ext_model: ClassVar[type[BaseModel]] = DkExt

    def pinpoint(self, c: ProvisionCitationInput) -> str:
        out: list[str] = []
        for part in c.num_path:
            kind, _, label = part.partition(":")
            if kind == "p":
                out.append(f"§§ {label}" if "-" in label else f"§ {label}")
            elif kind in ("stk", "nr", "litra"):
                out.append(f"{kind}. {label}" if kind != "litra" else f"litra {label}")
        return ", ".join(out)

    def provision_citation(self, c: ProvisionCitationInput) -> str:
        pin = self.pinpoint(c)
        act = next((p.partition(":")[2] for p in c.num_path if p.startswith("act:")), None)
        if act:  # reproduced commencement provision of an amending act
            return f"{pin} i {act}" if pin else act
        name = law_name(c.instrument_title)
        if not pin:
            return name
        return f"{pin}, i {name}" if "," in pin else f"{pin} i {name}"
