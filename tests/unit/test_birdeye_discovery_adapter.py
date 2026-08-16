"""
Unit tests for discovery/birdeye_discovery_adapter.py.

Only _sanitize_empty_structs() is tested here - it's a pure function.
discover_candidates() itself makes live HTTP calls and is verified via
manual live runs (see run_candidate_fetch.py usage), not mocked here.
"""

from __future__ import annotations

from discovery.birdeye_discovery_adapter import _sanitize_empty_structs


def test_sanitize_empty_structs_replaces_empty_dict_with_none():
    item = {"address": "abc", "extensions": {}, "symbol": "TEST"}
    sanitized = _sanitize_empty_structs(item)
    assert sanitized == {"address": "abc", "extensions": None, "symbol": "TEST"}


def test_sanitize_empty_structs_leaves_populated_dict_untouched():
    item = {"address": "abc", "extensions": {"twitter": "https://x.com/abc"}}
    sanitized = _sanitize_empty_structs(item)
    assert sanitized == item


def test_sanitize_empty_structs_leaves_non_dict_values_untouched():
    item = {"address": "abc", "liquidity": 5000.0, "holder": 100, "logo_uri": None}
    sanitized = _sanitize_empty_structs(item)
    assert sanitized == item
