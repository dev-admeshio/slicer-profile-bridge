"""Inheritance chain resolution.

Slicer profiles share defaults through an `inherits` field: a concrete
profile only declares the values it overrides, with `"nil"` as a sentinel
meaning "don't override, keep the parent's value". This module flattens
that chain into one dict of effective values, along with the list of
parents walked (so consumers can emit a full source audit trail).

Cycles (rare but possible in hand-edited user profiles) abort the walk
and the partial result is returned — failing loudly by raising would
take down bulk loads for one bad user file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from slicer_profile_bridge.loader import ProfileType, RawProfile, RawProfileIndex

# ── Sentinel handling ──────────────────────────────────────────────────
# Orca encodes "inherit this value from parent" as the string "nil" at
# any position in a value. Single scalars, lists, and mixed shapes all
# use the same convention. We strip those during merge so a child with
# `"retract_length": ["nil", "nil"]` cleanly falls through to whatever
# the parent declared.


_NIL_STRINGS = frozenset({"nil", "NIL", "null"})


def _is_nil_value(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, str):
        return v in _NIL_STRINGS
    if isinstance(v, list) and v:
        return all(isinstance(x, str) and x in _NIL_STRINGS for x in v)
    return False


@dataclass
class ResolvedProfile:
    """A profile with inheritance flattened into a single dict of
    effective values, plus the parent chain for traceability.
    """

    type: ProfileType
    name: str
    vendor: str
    data: dict[str, Any]              # merged values
    inherits_chain: list[str]         # root-most last


def resolve(profile: RawProfile, index: RawProfileIndex) -> ResolvedProfile:
    """Flatten a profile's inheritance into effective values.

    Supports both shapes:
      * Single-parent chain (Orca JSON: `"inherits": "parent"`).
      * Multi-parent merge (Prusa INI: `inherits = a; b`). Parents are
        applied left-to-right, so a later parent overrides an earlier
        parent for keys both declare. The profile itself overrides all
        parents last.

    The merge pre-strips `nil` sentinels from each layer so a child can
    opt out of an override by writing `"nil"` instead of the value.

    Cycles in the inherits DAG are detected per recursion and silently
    broken — the partial resolution continues instead of raising so one
    bad user profile doesn't sink a bulk load.
    """
    cache: dict[str, dict[str, Any]] = {}
    chain_accumulator: list[str] = []
    seen_during_walk: set[str] = set()

    def _resolve_layer(p: RawProfile) -> dict[str, Any]:
        if p.name in cache:
            return cache[p.name]
        if p.name in seen_during_walk:
            return {}  # cycle — drop this branch
        seen_during_walk.add(p.name)

        merged: dict[str, Any] = {}
        for parent_name in p.inherits_all:
            parent = index.get(profile.type, parent_name)
            if parent is None:
                continue
            # Record the parent BEFORE recursing — the chain goes
            # immediate-first, root-last, matching how humans read
            # ancestry ("this inherits from X which inherits from Y").
            if parent_name not in chain_accumulator and parent is not profile:
                chain_accumulator.append(parent_name)
            parent_data = _resolve_layer(parent)
            merged.update(parent_data)

        for key, value in p.data.items():
            if _is_nil_value(value):
                continue
            merged[key] = value

        seen_during_walk.discard(p.name)
        cache[p.name] = merged
        return merged

    merged_data = _resolve_layer(profile)

    return ResolvedProfile(
        type=profile.type,
        name=profile.name,
        vendor=profile.vendor,
        data=merged_data,
        inherits_chain=chain_accumulator,
    )
