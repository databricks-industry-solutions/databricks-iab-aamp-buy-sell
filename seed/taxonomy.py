"""Trust→tier map (mirrors the SDK) + optional IAB taxonomy code validation.

`TRUST_TO_TIER_MAP` mirrors `seller-agent/models/agent_registry.py`. The IAB code
validation is optional: if the vendored taxonomy TSVs are present (point
`AAMP_TAXONOMY_DIR` at them, or drop them under `resources/taxonomies/`), codes
are checked against the real taxonomy; otherwise we validate internal coherence
only and emit a note. We never invent the mapping — it is the SDK's.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path

# Mirrors seller-agent TRUST_TO_TIER_MAP. BLOCKED → None (hard reject).
TRUST_TO_TIER_MAP: dict[str, str | None] = {
    "UNKNOWN": "public",
    "REGISTERED": "seat",
    "APPROVED": "agency",
    "PREFERRED": "advertiser",
    "BLOCKED": None,
}

_TAXO_FILES = {
    "iab-audience": "Audience Taxonomy 1.1.tsv",
    "iab-content": "Content Taxonomy 3.1.tsv",
}


def _taxonomy_dir() -> Path | None:
    env = os.environ.get("AAMP_TAXONOMY_DIR")
    if env and Path(env).is_dir():
        return Path(env)
    local = Path(__file__).resolve().parent.parent / "resources" / "taxonomies"
    return local if local.is_dir() else None


def load_valid_codes() -> dict[str, set[str]] | None:
    """Return {taxonomy: {codes}} if vendored TSVs exist, else None (skip check)."""
    d = _taxonomy_dir()
    if not d:
        return None
    out: dict[str, set[str]] = {}
    for taxo, fname in _TAXO_FILES.items():
        path = d / fname
        if not path.exists():
            continue
        codes: set[str] = set()
        with path.open(encoding="utf-8", newline="") as fh:
            uid: int | None = None  # column index of "Unique ID" (varies per TSV; some have a preamble row)
            for row in csv.reader(fh, delimiter="\t"):
                if uid is None:
                    if "Unique ID" in row:
                        uid = row.index("Unique ID")
                    continue  # skip preamble/header rows until we locate the column
                if len(row) > uid and row[uid].strip():
                    codes.add(row[uid].strip())
        out[taxo] = codes
    return out or None
