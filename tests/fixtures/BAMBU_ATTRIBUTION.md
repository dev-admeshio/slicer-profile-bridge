# Bambu fixtures — attribution

Profile JSON files under `tests/fixtures/bambu/` are unmodified copies
of profiles distributed with [BambuStudio](https://github.com/bambulab/BambuStudio),
licensed under **AGPL-3.0**.

Included fixtures (Bambu Lab X1 Carbon family):

| Path | Source |
|---|---|
| `bambu/BBL/machine/Bambu Lab X1 Carbon 0.4 nozzle.json` | `BambuStudio/resources/profiles/BBL/machine/` |
| `bambu/BBL/machine/fdm_bbl_3dp_001_common.json` | same |
| `bambu/BBL/machine/fdm_machine_common.json` | same |
| `bambu/BBL/filament/Bambu PLA Basic @BBL X1C.json` | `BambuStudio/resources/profiles/BBL/filament/` |
| `bambu/BBL/filament/Bambu PLA Basic @base.json` | same |
| `bambu/BBL/filament/fdm_filament_pla.json` | same |
| `bambu/BBL/filament/fdm_filament_common.json` | same |
| `bambu/BBL/process/0.20mm Standard @BBL X1C.json` | `BambuStudio/resources/profiles/BBL/process/` |
| `bambu/BBL/process/fdm_process_single_0.20.json` | same |
| `bambu/BBL/process/fdm_process_single_common.json` | same |
| `bambu/BBL/process/fdm_process_common.json` | same |

The fixtures are present only to exercise the Bambu translator against
real-world inputs; they are not redistributed as part of the library
itself.

**License**: AGPL-3.0 for the profile files. See the BambuStudio
repository LICENSE for the full terms.
