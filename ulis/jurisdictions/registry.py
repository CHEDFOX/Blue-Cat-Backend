"""Loads, validates and navigates the jurisdiction tree."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import yaml

from ulis.jurisdictions.profile import Court, JurisdictionProfile


class ProfileError(ValueError):
    pass


@dataclass
class JurisdictionRegistry:
    profiles: dict[str, JurisdictionProfile] = field(default_factory=dict)
    # Built on first use: the profiles are not changed after loading, and the US profile alone lists hundreds of
    # courts, so the appeal chains asked for on every search hit must not rebuild the court table each time.
    _court_table: dict[str, Court] | None = field(default=None, init=False, repr=False, compare=False)
    _court_home: dict[str, str] = field(default_factory=dict, init=False, repr=False, compare=False)

    # ------------------------------------------------------------------ loading
    @classmethod
    def from_directory(cls, directory: Path) -> JurisdictionRegistry:
        profiles: dict[str, JurisdictionProfile] = {}
        for path in sorted(directory.glob("*.yaml")):
            with path.open("r", encoding="utf-8") as fh:
                raw = yaml.safe_load(fh)
            try:
                profile = JurisdictionProfile.model_validate(raw)
            except ValueError as exc:
                raise ProfileError(f"{path.name}: {exc}") from exc
            if profile.id in profiles:
                raise ProfileError(f"{path.name}: duplicate jurisdiction id {profile.id}")
            profiles[profile.id] = profile
        registry = cls(profiles)
        registry.validate()
        return registry

    def validate(self) -> None:
        for profile in self.profiles.values():
            if profile.parent is not None and profile.parent not in self.profiles:
                raise ProfileError(f"{profile.id}: unknown parent {profile.parent}")
            for membership in profile.memberships:
                org = self.profiles.get(membership.organisation)
                if org is None or org.kind != "regional_organisation":
                    raise ProfileError(f"{profile.id}: membership of unknown organisation {membership.organisation}")
            # Detect cycles in the parent chain.
            seen: set[str] = set()
            node: str | None = profile.id
            while node is not None:
                if node in seen:
                    raise ProfileError(f"{profile.id}: cycle in jurisdiction hierarchy")
                seen.add(node)
                node = self.profiles[node].parent
        all_courts = self.courts()
        for court in all_courts.values():
            for target in court.appeals_to:
                if target not in all_courts:
                    raise ProfileError(f"court {court.id}: appeals_to unknown court {target}")

    # --------------------------------------------------------------- navigation
    def get(self, jurisdiction_id: str) -> JurisdictionProfile:
        try:
            return self.profiles[jurisdiction_id]
        except KeyError as exc:
            raise KeyError(f"unknown jurisdiction {jurisdiction_id!r}") from exc

    def __contains__(self, jurisdiction_id: object) -> bool:
        return jurisdiction_id in self.profiles

    def ancestors(self, jurisdiction_id: str) -> list[str]:
        """Parent chain, nearest first, excluding the jurisdiction itself."""
        chain: list[str] = []
        parent = self.get(jurisdiction_id).parent
        while parent is not None:
            chain.append(parent)
            parent = self.profiles[parent].parent
        return chain

    def chain(self, jurisdiction_id: str) -> list[str]:
        """The jurisdiction followed by its ancestors."""
        return [jurisdiction_id, *self.ancestors(jurisdiction_id)]

    def children(self, jurisdiction_id: str) -> list[str]:
        return sorted(p.id for p in self.profiles.values() if p.parent == jurisdiction_id)

    def descendants(self, jurisdiction_id: str) -> list[str]:
        out: list[str] = []
        stack = self.children(jurisdiction_id)
        while stack:
            node = stack.pop(0)
            out.append(node)
            stack.extend(self.children(node))
        return out

    def sovereign(self, jurisdiction_id: str) -> str | None:
        """The sovereign state a jurisdiction belongs to, if any."""
        for node in self.chain(jurisdiction_id):
            if self.profiles[node].kind == "sovereign_state":
                return node
        return None

    def organisations_on(self, jurisdiction_id: str, when: date) -> list[str]:
        """Regional organisations the jurisdiction's State is a member of on ``when`` (per profile)."""
        state = self.sovereign(jurisdiction_id)
        if state is None:
            return []
        return [
            m.organisation
            for m in self.profiles[state].memberships
            if (m.from_date is None or m.from_date <= when) and (m.to_date is None or when < m.to_date)
        ]

    def domestic_chain(self, jurisdiction_id: str) -> list[str]:
        """The chain up to (and including) the sovereign state; excludes international roots."""
        out: list[str] = []
        for node in self.chain(jurisdiction_id):
            out.append(node)
            if self.profiles[node].kind in ("sovereign_state", "regional_organisation"):
                break
        return [n for n in out if self.profiles[n].kind != "international_legal_order"]

    def extent_covers(self, extent: Iterable[str] | None, target: str) -> bool:
        """Whether an instrument whose territorial extent is ``extent`` applies in ``target``.

        An extent entry covers the target if it is the target itself or one of its ancestors
        (a federal Act extending to the whole federation applies in every province).
        """
        if not extent:
            return True
        chain = set(self.chain(target))
        return any(e in chain for e in extent)

    # ------------------------------------------------------------------ courts
    def courts(self) -> dict[str, Court]:
        if self._court_table is None:
            out: dict[str, Court] = {}
            home: dict[str, str] = {}
            for profile in self.profiles.values():
                for court in profile.courts:
                    if court.id in out:
                        raise ProfileError(f"court {court.id} defined in more than one profile")
                    out[court.id] = court
                    home[court.id] = profile.id
            self._court_table, self._court_home = out, home
        return self._court_table

    def court_jurisdiction(self, court_id: str) -> str:
        self.courts()
        try:
            return self._court_home[court_id]
        except KeyError:
            raise KeyError(f"unknown court {court_id!r}") from None

    def courts_above(self, court_id: str) -> list[str]:
        """Courts to which appeals from ``court_id`` lie, directly or indirectly."""
        courts = self.courts()
        out: list[str] = []
        stack = list(courts[court_id].appeals_to)
        while stack:
            node = stack.pop(0)
            if node not in out:
                out.append(node)
                stack.extend(courts[node].appeals_to)
        return out

    def courts_below(self, court_id: str) -> list[str]:
        return sorted(c for c in self.courts() if court_id in self.courts_above(c))

    def tree(self) -> list[dict[str, object]]:
        def node(pid: str) -> dict[str, object]:
            p = self.profiles[pid]
            return {
                "id": p.id,
                "name": p.name,
                "kind": p.kind,
                "fictional": p.fictional,
                "children": [node(c) for c in self.children(pid)],
            }

        return [node(p.id) for p in self.profiles.values() if p.parent is None]
