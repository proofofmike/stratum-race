"""Tests for standalone/main.py orchestration, startup, and validation.

Covers config validation (missing/invalid pools, bad port, pool group mismatch),
argument parsing defaults and custom values, and cold-start file creation.

Requirements: 3.9, 10.1, 10.2, 10.3, 10.4, 10.5, 11.1, 11.2, 11.3,
              11.4, 11.5, 11.6, 15.1, 15.2, 15.3, 15.4, 15.5, 15.6
"""

import argparse
import json
from pathlib import Path

import pytest

from standalone.main import parse_args, validate_config
from lib.local_store import LocalStorage


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_args(tmp_path, pools_content=None, port=8080, pool_group="all", pools_path=None):
    """Build an argparse.Namespace with a pools file on disk."""
    if pools_path is None:
        pools_file = tmp_path / "pools.json"
    else:
        pools_file = Path(pools_path)

    if pools_content is not None:
        pools_file.parent.mkdir(parents=True, exist_ok=True)
        pools_file.write_text(
            pools_content if isinstance(pools_content, str) else json.dumps(pools_content),
            encoding="utf-8",
        )

    return argparse.Namespace(
        host="0.0.0.0",
        port=port,
        data_dir=str(tmp_path / "data"),
        pools=str(pools_file),
        pool_group=pool_group,
        vantage="local",
        frontend_dir=None,
    )


VALID_POOLS = {
    "pools": [
        {
            "name": "pool_a",
            "display_name": "Pool A",
            "host": "pool-a.example.com",
            "port": 3333,
            "groups": ["all", "solo"],
        }
    ]
}


# ------------------------------------------------------------------
# validate_config — exit cases (R11.4, R11.5, R11.6)
# ------------------------------------------------------------------


class TestValidateConfigMissingPoolsFile:
    def test_validate_config_missing_pools_file_exits(self, tmp_path):
        """Missing pools file causes SystemExit(1)."""
        args = _make_args(tmp_path, pools_path=str(tmp_path / "nonexistent.json"))
        with pytest.raises(SystemExit) as exc_info:
            validate_config(args)
        assert exc_info.value.code == 1


class TestValidateConfigInvalidJson:
    def test_validate_config_invalid_json_exits(self, tmp_path):
        """Bad JSON in pools file causes SystemExit(1)."""
        args = _make_args(tmp_path, pools_content="not valid json {{{")
        with pytest.raises(SystemExit) as exc_info:
            validate_config(args)
        assert exc_info.value.code == 1


class TestValidateConfigMissingFields:
    def test_validate_config_missing_fields_exits(self, tmp_path):
        """Valid JSON but missing required pool fields causes SystemExit(1)."""
        incomplete_pools = {
            "pools": [
                {
                    "name": "pool_a",
                    # missing display_name, host, port, groups
                }
            ]
        }
        args = _make_args(tmp_path, pools_content=incomplete_pools)
        with pytest.raises(SystemExit) as exc_info:
            validate_config(args)
        assert exc_info.value.code == 1


class TestValidateConfigPoolGroupNotFound:
    def test_validate_config_pool_group_not_found_exits(self, tmp_path):
        """Pool group filter that doesn't match any pool causes SystemExit(1)."""
        args = _make_args(tmp_path, pools_content=VALID_POOLS, pool_group="nonexistent")
        with pytest.raises(SystemExit) as exc_info:
            validate_config(args)
        assert exc_info.value.code == 1


class TestValidateConfigValid:
    def test_validate_config_valid_pools_returns_list(self, tmp_path):
        """Valid config returns the filtered pool list."""
        args = _make_args(tmp_path, pools_content=VALID_POOLS)
        result = validate_config(args)
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["name"] == "pool_a"


# ------------------------------------------------------------------
# Port validation (R3.4, R8.6)
# ------------------------------------------------------------------


class TestInvalidPort:
    def test_invalid_port_zero_exits(self, tmp_path):
        """Port 0 causes SystemExit(1)."""
        args = _make_args(tmp_path, pools_content=VALID_POOLS, port=0)
        with pytest.raises(SystemExit) as exc_info:
            validate_config(args)
        assert exc_info.value.code == 1

    def test_invalid_port_over_max_exits(self, tmp_path):
        """Port 99999 causes SystemExit(1)."""
        args = _make_args(tmp_path, pools_content=VALID_POOLS, port=99999)
        with pytest.raises(SystemExit) as exc_info:
            validate_config(args)
        assert exc_info.value.code == 1


# ------------------------------------------------------------------
# Cold start — ensure_initial_files (R3.9, Property 10)
# ------------------------------------------------------------------


class TestColdStartEndpoints:
    def test_cold_start_endpoints_return_valid_json(self, tmp_path):
        """After ensure_initial_files, data files are valid JSON."""
        data_dir = tmp_path / "data"
        storage = LocalStorage(data_dir)
        storage.ensure_initial_files()

        # recent-blocks.json must be a valid JSON array
        recent_path = data_dir / "api" / "recent" / "recent-blocks.json"
        assert recent_path.exists()
        recent_data = json.loads(recent_path.read_text(encoding="utf-8"))
        assert isinstance(recent_data, list)
        assert recent_data == []

        # latest.json must be valid JSON with height/epoch
        latest_path = data_dir / "api" / "latest.json"
        assert latest_path.exists()
        latest_data = json.loads(latest_path.read_text(encoding="utf-8"))
        assert latest_data == {"height": None, "epoch": None}

        # Aggregate files must be valid JSON
        aggregate_files = [
            data_dir / "api" / "aggregates" / "recent-10.json",
            data_dir / "api" / "aggregates" / "recent-50.json",
            data_dir / "api" / "aggregates" / "last-24h.json",
            data_dir / "api" / "aggregates" / "last-7d.json",
        ]
        for agg_path in aggregate_files:
            assert agg_path.exists(), f"Missing: {agg_path}"
            agg_data = json.loads(agg_path.read_text(encoding="utf-8"))
            assert agg_data["total_races"] == 0
            assert agg_data["pools"] == {}


# ------------------------------------------------------------------
# Argument parsing (R8.2, R8.3)
# ------------------------------------------------------------------


class TestParseArgsDefaults:
    def test_parse_args_defaults(self):
        """Default values for all arguments are correct."""
        args = parse_args([])
        assert args.host == "0.0.0.0"
        assert args.port == 8080
        assert args.data_dir == "./data"
        assert args.pools == "config/pools.json"
        assert args.pool_group == "all"
        assert args.vantage == "local"
        assert args.frontend_dir is None


class TestParseArgsCustom:
    def test_parse_args_custom(self):
        """Custom flag values are correctly parsed."""
        args = parse_args([
            "--host", "127.0.0.1",
            "--port", "9090",
            "--data-dir", "/tmp/my-data",
            "--pools", "/etc/pools.json",
            "--pool-group", "solo",
            "--vantage", "my-node",
            "--frontend-dir", "/opt/frontend/dist",
        ])
        assert args.host == "127.0.0.1"
        assert args.port == 9090
        assert args.data_dir == "/tmp/my-data"
        assert args.pools == "/etc/pools.json"
        assert args.pool_group == "solo"
        assert args.vantage == "my-node"
        assert args.frontend_dir == "/opt/frontend/dist"
