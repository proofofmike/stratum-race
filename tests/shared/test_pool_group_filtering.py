"""Property test for pool group filtering.

**Validates: Requirements 21.3**

Property 17: Pool group filtering selects correct subset
For any pool configuration containing pools with various group tags, and any
group filter value, the Collector SHALL select exactly those pools whose groups
array contains the filter value.
"""

import json
import sys
import importlib.util
from pathlib import Path

import pytest
from hypothesis import given, settings, assume
from hypothesis import strategies as st

# Import collector/str_race.py explicitly to avoid the root str_race.py
_collector_module_path = Path(__file__).parent.parent.parent / "collector" / "str_race.py"
_spec = importlib.util.spec_from_file_location("collector_str_race", _collector_module_path)
_collector_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_collector_mod)

_load_central_pool_config = _collector_mod._load_central_pool_config
PoolConfig = _collector_mod.PoolConfig


# --- Strategies ---

# Group tags that pools can belong to
GROUP_TAGS = ["all", "americas", "europe", "asia", "solo", "big", "small"]

# Strategy for a single pool entry matching the pools.json schema
pool_entry_strategy = st.fixed_dictionaries({
    "name": st.from_regex(r"[a-z][a-z0-9_]{1,15}", fullmatch=True),
    "display_name": st.text(min_size=1, max_size=20),
    "host": st.from_regex(r"[a-z][a-z0-9.]{2,29}", fullmatch=True),
    "port": st.integers(min_value=1, max_value=65535),
    "groups": st.lists(
        st.sampled_from(GROUP_TAGS),
        min_size=1,
        max_size=4,
        unique=True,
    ),
})


# --- Property Test ---

@given(
    pools=st.lists(
        pool_entry_strategy,
        min_size=1,
        max_size=20,
        unique_by=lambda p: p["name"],
    ),
    filter_group=st.sampled_from(GROUP_TAGS + ["nonexistent"]),
)
@settings(max_examples=200)
def test_pool_group_filtering_selects_correct_subset(pools, filter_group):
    """Property 17: Pool group filtering selects correct subset.

    For any pool configuration and any group filter, _load_central_pool_config
    returns exactly those pools whose 'groups' array contains the filter value.

    **Validates: Requirements 21.3**
    """
    import tempfile
    import os

    # Build a valid config JSON
    config = {
        "version": 1,
        "updated_utc": "2025-01-15T00:00:00Z",
        "pools": pools,
    }

    # Write to a temp file
    fd, config_path = tempfile.mkstemp(suffix=".json")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(config, f)

        # Compute expected: pools whose groups contain the filter_group
        expected_names = {p["name"] for p in pools if filter_group in p["groups"]}

        if not expected_names:
            # When no pools match the filter, function should raise SystemExit
            with pytest.raises(SystemExit) as exc_info:
                _load_central_pool_config(config_path, filter_group)
            assert "No pools match group filter" in str(exc_info.value)
        else:
            result = _load_central_pool_config(config_path, filter_group)

            # Verify result type
            assert all(isinstance(pc, PoolConfig) for pc in result)

            # Verify exactly the correct subset is selected
            result_names = {pc.name for pc in result}
            assert result_names == expected_names

            # Verify each returned PoolConfig has correct host/port from source
            source_lookup = {p["name"]: p for p in pools}
            for pc in result:
                source = source_lookup[pc.name]
                assert pc.host == source["host"]
                assert pc.port == source["port"]
    finally:
        os.unlink(config_path)
