"""The catalog: jurisdiction profiles, source descriptors and concept labels, cross-validated."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from ulis.jurisdictions.modules import JurisdictionModule, get_module
from ulis.jurisdictions.registry import JurisdictionRegistry, ProfileError
from ulis.sources.registry import SourceRegistry


class ConceptDef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    key: str
    description: str
    scope: str | None = None
    labels: dict[str, list[str]] = Field(default_factory=dict)


class ConceptFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1]
    concepts: list[ConceptDef]


@dataclass
class Catalog:
    jurisdictions: JurisdictionRegistry
    sources: SourceRegistry
    concepts: list[ConceptDef]

    @classmethod
    def load(cls, config_dir: Path) -> Catalog:
        jurisdictions = JurisdictionRegistry.from_directory(config_dir / "jurisdictions")
        sources = SourceRegistry.from_directory(config_dir / "sources")
        concepts: list[ConceptDef] = []
        for path in sorted((config_dir / "concepts").glob("*.yaml")):
            concepts.extend(ConceptFile.model_validate(yaml.safe_load(path.read_text("utf-8"))).concepts)
        catalog = cls(jurisdictions, sources, concepts)
        catalog.validate()
        return catalog

    def validate(self) -> None:
        for source in self.sources:
            if source.status == "active":
                missing = [j for j in source.jurisdictions if j not in self.jurisdictions]
                if missing:
                    raise ProfileError(f"active source {source.id} covers unknown jurisdictions {missing}")
        for profile in self.jurisdictions.profiles.values():
            for source_id in profile.publication.official_sources:
                if source_id not in self.sources.sources:
                    raise ProfileError(f"{profile.id}: unknown official source {source_id}")
            module = get_module(profile.citation.module)
            module.validate_profile_extensions(profile.extensions)
        keys = [c.key for c in self.concepts]
        if len(keys) != len(set(keys)):
            raise ProfileError("duplicate concept keys")

    def module_for(self, jurisdiction_id: str) -> JurisdictionModule:
        return get_module(self.jurisdictions.get(jurisdiction_id).citation.module)
