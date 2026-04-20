"""Translate upstream slicer profile trees into a single canonical JSON.

Used by the `.github/workflows/sync-upstream.yml` job; also runnable
locally for offline regeneration.

Output shape:

    {
      "generated_at": "2026-04-20T06:00:00Z",
      "slicers": {
        "orca":  {"printers": {...}, "filaments": {...}, "processes": {...}},
        "prusa": {...},
        "bambu": {...}
      }
    }

The result is a portable flat file downstream consumers can load without
needing to run the bridge themselves — point Admeshio at one URL, done.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

from slicer_profile_bridge import (
    ProfileBundle,
    load_bambu,
    load_orca,
    load_prusa,
)


def _bundle_to_dict(bundle: ProfileBundle) -> dict[str, Any]:
    return {
        "slicer": bundle.slicer,
        "slicer_version": bundle.slicer_version,
        "printers": {k: v.model_dump(mode="json") for k, v in bundle.printers.items()},
        "filaments": {k: v.model_dump(mode="json") for k, v in bundle.filaments.items()},
        "processes": {k: v.model_dump(mode="json") for k, v in bundle.processes.items()},
    }


def _summary_row(slicer: str, bundle: ProfileBundle) -> str:
    return (
        f"| {slicer} "
        f"| {len(bundle.printers)} "
        f"| {len(bundle.filaments)} "
        f"| {len(bundle.processes)} |"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--orca", type=Path, required=True,
                        help="OrcaSlicer resources/profiles directory")
    parser.add_argument("--prusa", type=Path, required=True,
                        help="PrusaSlicer resources/profiles directory")
    parser.add_argument("--bambu", type=Path, required=True,
                        help="BambuStudio resources/profiles directory")
    parser.add_argument("--output", type=Path, required=True,
                        help="canonical-profiles.json output path")
    parser.add_argument("--summary", type=Path,
                        help="Optional Markdown summary path (sync stats)")
    args = parser.parse_args(argv)

    bundles: dict[str, ProfileBundle] = {
        "orca":  load_orca(args.orca),
        "prusa": load_prusa(args.prusa),
        "bambu": load_bambu(args.bambu),
    }

    payload = {
        "schema_version": 1,
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "slicers": {name: _bundle_to_dict(b) for name, b in bundles.items()},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False),
        encoding="utf-8",
    )

    if args.summary is not None:
        args.summary.parent.mkdir(parents=True, exist_ok=True)
        lines = [
            "# Canonical profile sync",
            "",
            f"Generated at `{payload['generated_at']}`",
            "",
            "| Slicer | Printers | Filaments | Processes |",
            "|---|---|---|---|",
            *[_summary_row(name, b) for name, b in bundles.items()],
        ]
        args.summary.write_text("\n".join(lines) + "\n", encoding="utf-8")

    total = sum(
        len(b.printers) + len(b.filaments) + len(b.processes)
        for b in bundles.values()
    )
    print(f"canonical-profiles.json: {total} profiles across 3 slicers", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
