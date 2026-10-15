"""Utah module: ``Utah Code § 76-5-203`` (the official Utah Code, title-chapter-section; not the annotated edition)."""

from __future__ import annotations

from typing import ClassVar

from pydantic import BaseModel, ConfigDict

from ulis.jurisdictions.modules.base import ProvisionCitationInput
from ulis.jurisdictions.modules.us_state_base import UsStateModule


class UsUtExt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str
    title_name: str
    compiled_effective: str | None = None


class UsUtModule(UsStateModule):
    id: ClassVar[str] = "us_ut"
    version: ClassVar[str] = "1.0.0"
    ext_model: ClassVar[type[BaseModel]] = UsUtExt

    def code_citation(self, c: ProvisionCitationInput) -> str:
        return "Utah Code"

    def provision_citation(self, c: ProvisionCitationInput) -> str:
        if not [p for p in c.num_path if p]:
            return f"Utah Code title {c.ext.get('title', c.instrument_title)}"
        return super().provision_citation(c)
