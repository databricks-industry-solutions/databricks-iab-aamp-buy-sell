"""IAB Audience Taxonomy index + validation over `resources/taxonomies/*.tsv`.

The L3 audience_planner proposes IAB Audience Taxonomy 1.1 codes from a brief's
free-text interests; this module is the gate that keeps the LLM honest — every
proposed code is checked against the vendored TSV. If the TSV isn't vendored, the
index reports `available=False` and the planner degrades to internal coherence only.

TSV shape (tab-delimited, a leading empty column):
  "" | Unique ID | Parent ID | Condensed Name (1st, 2nd, Last Tier) | Tier 1..6 | *Extension Notes
"""

from __future__ import annotations

import csv
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

_AUDIENCE_TSV = "Audience Taxonomy 1.1.tsv"
_STOP = {"the", "a", "an", "of", "and", "&", "to", "for", "with", "on", "in"}


def _tokens(s: str) -> set[str]:
    return {w for w in re.sub(r"[^a-z0-9 ]", " ", s.lower()).split() if w and w not in _STOP}


def taxonomy_dir() -> Optional[Path]:
    """Locate the vendored TSVs: AAMP_TAXONOMY_DIR, then the repo's resources/, then a
    copy vendored INTO the app dir (deploy packages them under buyer/resources/)."""
    env = os.environ.get("AAMP_TAXONOMY_DIR")
    if env and Path(env).is_dir():
        return Path(env)
    here = Path(__file__).resolve()  # buyer/src/buyer_agent/taxonomy.py
    candidates = [
        here.parents[3] / "resources" / "taxonomies",  # repo root (local dev)
        here.parents[2] / "resources" / "taxonomies",  # buyer/resources (vendored for deploy)
    ]
    return next((c for c in candidates if c.is_dir()), None)


@dataclass(frozen=True)
class TaxonomyIndex:
    by_code: dict[str, dict]            # code -> {"code","name","leaf","tiers"}
    token_index: dict[str, frozenset]  # token -> {codes}
    available: bool

    def validate(self, code: str) -> bool:
        return str(code).strip() in self.by_code

    def name(self, code: str) -> Optional[str]:
        row = self.by_code.get(str(code).strip())
        return row["name"] if row else None

    def search(self, query: str, limit: int = 8) -> list[dict]:
        """Candidate {code, name} pairs ranked by token overlap (most specific first)."""
        q = _tokens(query)
        if not q:
            return []
        scored: dict[str, int] = {}
        for tok in q:
            for code in self.token_index.get(tok, ()):  # type: ignore[union-attr]
                scored[code] = scored.get(code, 0) + 1
        ranked = sorted(
            scored.items(),
            key=lambda kv: (-kv[1], len(self.by_code[kv[0]]["name"]),
                            int(kv[0]) if kv[0].isdigit() else 10**9),
        )
        return [{"code": c, "name": self.by_code[c]["name"]} for c, _ in ranked[:limit]]


def _clean_name(condensed: str, tiers: list[str]) -> str:
    name = condensed or " | ".join(tiers)
    name = name.replace("*", "").strip()
    while name.endswith("|"):
        name = name[:-1].strip()
    return name


@lru_cache(maxsize=4)
def _parse(path_str: str) -> tuple[dict, dict]:
    by_code: dict[str, dict] = {}
    token_index: dict[str, set[str]] = {}
    with Path(path_str).open(encoding="utf-8", newline="") as fh:
        uid = name_i = None
        tier_idx: list[int] = []
        for row in csv.reader(fh, delimiter="\t"):
            if uid is None:  # find the header row (some TSVs have a preamble)
                if "Unique ID" in row:
                    uid = row.index("Unique ID")
                    name_i = next((i for i, c in enumerate(row) if c.startswith("Condensed Name")), None)
                    tier_idx = [i for i, c in enumerate(row) if c.startswith("Tier ")]
                continue
            if len(row) <= uid or not row[uid].strip():
                continue
            code = row[uid].strip()
            tiers = [row[i].strip() for i in tier_idx if i < len(row) and row[i].strip()]
            condensed = row[name_i].strip() if (name_i is not None and name_i < len(row)) else ""
            name = _clean_name(condensed, tiers)
            leaf = tiers[-1] if tiers else name
            by_code[code] = {"code": code, "name": name, "leaf": leaf, "tiers": tiers}
            for tok in _tokens(name) | _tokens(leaf):
                token_index.setdefault(tok, set()).add(code)
    return by_code, {k: frozenset(v) for k, v in token_index.items()}


def _index_for(fname: str) -> TaxonomyIndex:
    d = taxonomy_dir()
    if not d or not (d / fname).exists():
        return TaxonomyIndex({}, {}, available=False)
    by_code, token_index = _parse(str(d / fname))
    return TaxonomyIndex(by_code, token_index, available=True)


def audience_index() -> TaxonomyIndex:
    return _index_for(_AUDIENCE_TSV)
