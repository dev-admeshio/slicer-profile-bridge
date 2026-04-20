# Changelog

All notable changes to `slicer-profile-bridge` are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
this project follows [semver](https://semver.org/). Everything on the
`0.x` line should be treated as schema-unstable — we lock the shape at
`1.0.0` once the third real consumer pushes back on it.

## [Unreleased]

## [0.2.0] — 2026-04-20

### Added

- `raw_data: dict[str, Any]` on `CanonicalPrinter`, `CanonicalFilament`,
  and `CanonicalProcess`. Carries every vendor field the canonical
  schema doesn't promote to first-class, minus inheritance-metadata
  keys (`type`, `name`, `inherits`, `setting_id`, etc.). Consumers can
  read obscure vendor fields without a bridge PR for every new audit
  check.
- `ProcessSpeeds.small_perimeter`, `.gap_infill`, `.bridge` — three
  speeds commonly audited for surface quality / cooling risk that were
  previously only reachable through `raw_data`.
- `CanonicalProcess.default_acceleration_mm_s2`, `retraction_overrides`
  — process-level motion knobs Prusa and Orca both carry.
- `.github/workflows/sync-upstream.yml` + `scripts/sync_upstream.py` —
  scheduled (daily) translation of OrcaSlicer / PrusaSlicer / BambuStudio
  HEAD into a single `canonical-profiles.json` artifact plus a
  `profiles-data` branch that downstream consumers can pull from.
- `src/slicer_profile_bridge/translators/README.md` — plugin-style
  contract for adding a new slicer translator (Cura, Lychee, ChiTuBox).

### Changed

- Translator output now includes `raw_data` by default — downstream
  consumers that did `CanonicalPrinter.model_dump()` get a strictly
  larger payload. Anything parsing with `extra="forbid"` on the
  canonical models must add the new field to its schema.

### Rationale

Feedback on 0.1.0: bridge should be a faithful translator, not an
opinionated extractor. Consumers with diverse needs (Admeshio auditing,
farm scheduling, cost estimation) each want a slightly different cut of
the vendor data. `raw_data` gives all of them read-access without
forcing a canonical-schema expansion for every new field request.

## [0.1.0] — 2026-04-20

### Added

- Canonical schema in pydantic v2: `CanonicalPrinter`,
  `CanonicalFilament`, `CanonicalProcess`, `CanonicalRecipe`,
  `ProfileBundle`. SI units throughout, `extra="forbid"` on every
  model.
- `SourceMetadata` on every top-level object — carries slicer, vendor,
  original profile id, and resolved `inherits_chain` for traceability.
- OrcaSlicer translator (Phase 1): walks `resources/profiles/` tree,
  resolves single-parent inheritance, maps the Orca JSON field names
  into canonical. Handles Orca's `"nil"` sentinel, percentage strings,
  list-wrapping for multi-extruder profiles, bed polygon parsing.
- PrusaSlicer translator (Phase 2): multi-section INI parser, multi-
  parent inheritance (`inherits = a; b`), abstract-base filter for
  `*wrapped*` names.
- BambuStudio translator (Phase 3): thin re-label over the Orca
  translator — Bambu's JSON is upstream of Orca so only the
  `source.slicer` label changes.
- 65 tests across schema, all three translators, and cross-slicer
  consistency. CI on Python 3.10 / 3.11 / 3.12 with ruff + mypy-strict
  + pytest + coverage.
- Fixtures: Bambu X1 Carbon family, Prusa MK4 / Prusament PLA /
  0.20mm QUALITY, Bambu X1C family. Attribution notes alongside.
