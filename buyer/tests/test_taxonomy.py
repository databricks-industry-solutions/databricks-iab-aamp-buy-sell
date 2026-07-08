"""Deterministic tests for the IAB taxonomy index + the TaxonomyLookupTool gate.

No LLM. Requires the vendored TSV; auto-skips if absent."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from buyer_agent.taxonomy import audience_index  # noqa: E402
from buyer_agent.tools import TaxonomyLookupTool  # noqa: E402

_idx = audience_index()
needs_tsv = pytest.mark.skipif(not _idx.available, reason="Audience Taxonomy TSV not vendored (make taxonomies)")


@needs_tsv
def test_validate_known_codes():
    assert _idx.validate("607")          # Interest | Sports
    assert _idx.validate("1404")         # Purchase Intent | Food and Beverage Services | Fast Food
    assert not _idx.validate("9999999")  # invented
    assert not _idx.validate("")


@needs_tsv
def test_name_lookup():
    assert "Sports" in (_idx.name("607") or "")
    assert "Fast Food" in (_idx.name("1404") or "")
    assert _idx.name("9999999") is None


@needs_tsv
@pytest.mark.parametrize("query,expected", [
    ("sports", "607"),
    ("fast food", "1404"),
    ("personal investing", "538"),
    ("television", "706"),
])
def test_search_finds_expected_code(query, expected):
    codes = {h["code"] for h in _idx.search(query, limit=8)}
    assert expected in codes, f"search('{query}') missing {expected}: {codes}"


@needs_tsv
def test_tool_returns_real_codes_as_json():
    out = TaxonomyLookupTool()._run("sports fans")
    hits = json.loads(out)
    assert hits and all(_idx.validate(h["code"]) for h in hits)


@needs_tsv
def test_tool_handles_no_match():
    out = TaxonomyLookupTool()._run("zzzqqq nonsense term")
    assert "No taxonomy matches" in out
