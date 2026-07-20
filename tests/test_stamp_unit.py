"""`_stamp` — the `_created_at` timestamping that makes time-range recall work.

No engine needed. The nesting under `timestamps` is not cosmetic: the engine
only feeds a field to its *timestamp* index if it arrives nested there. A plain
top-level date string is an ordinary kv string — exact-match only — so
`since`/`until` would silently match nothing.
"""

from __future__ import annotations

import os
import re

import pytest

ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$")


@pytest.fixture
def stamp(server):
    return server._stamp


def test_stamps_created_at_nested_under_timestamps(stamp):
    out = stamp({"text": "hello"})
    assert "timestamps" in out, "must nest, or the engine will not index it"
    assert ISO.match(out["timestamps"]["_created_at"])
    assert out["text"] == "hello"


def test_caller_supplied_created_at_is_hoisted_so_it_stays_queryable(stamp):
    """Historical imports carry their own date; it must end up in the timestamp
    index too, not sit at the top level as an unqueryable string."""
    out = stamp({"text": "old", "_created_at": "2020-01-01T00:00:00"})
    assert out["timestamps"]["_created_at"] == "2020-01-01T00:00:00"
    assert "_created_at" not in out, "should have moved, not been duplicated"


def test_unparseable_created_at_is_left_alone(stamp):
    """An entry the engine cannot parse fails the whole insert, so anything
    unrecognized stays an ordinary field rather than breaking the write."""
    out = stamp({"text": "x", "_created_at": "last tuesday"})
    assert out["_created_at"] == "last tuesday"
    assert "timestamps" not in out


def test_existing_timestamps_map_is_preserved(stamp):
    out = stamp({"text": "x", "timestamps": {"published": "2026-01-01T00:00:00"}})
    assert out["timestamps"]["published"] == "2026-01-01T00:00:00"
    assert ISO.match(out["timestamps"]["_created_at"])


def test_explicit_opt_out_skips_stamping(stamp):
    out = stamp({"text": "x"}, False)
    assert out == {"text": "x"}


def test_env_opt_out(stamp, monkeypatch):
    monkeypatch.setenv("MONTYCAT_AUTO_TIMESTAMP", "false")
    assert stamp({"text": "x"}) == {"text": "x"}


def test_input_is_not_mutated(stamp):
    original = {"text": "x"}
    stamp(original)
    assert original == {"text": "x"}, "caller's dict must not be modified in place"
