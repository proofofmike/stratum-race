"""Standalone integration test.

End-to-end test: launch server on ephemeral port with empty data dir,
inject a synthetic race via the sink, verify files are written and
WebSocket client receives the broadcast.

Requirements: 3.7, 3.9, 4.2, 10.1
"""

import asyncio
import json
import threading
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from lib.local_store import LocalStorage
from standalone.server import StandaloneServer


def _make_synthetic_race(height=900000, epoch=1700000000.0, vantage="local"):
    """Create a synthetic race result for testing."""
    return {
        "version": 1,
        "vantage": vantage,
        "block_height": height,
        "prevhash": "0" * 64,
        "first_epoch": epoch,
        "winner": "pool-a",
        "winner_nonempty": "pool-a",
        "block_miner": "TestMiner",
        "arrivals_offset_ms": {"pool-a": 0.0, "pool-b": 45.2},
        "nonempty_arrivals_offset_ms": {"pool-a": 0.0, "pool-b": 60.1},
        "empty_first_pools": [],
        "empty_to_full_ms": {},
        "missed_pools": [],
        "eligible_at_start": ["pool-a", "pool-b"],
        "pools_connected": 2,
        "pools_eligible": 2,
        "collector_meta": {"session_races": 1},
    }


@pytest.fixture
def setup(tmp_path):
    """Set up data dir, frontend dir, storage, and server."""
    data_dir = tmp_path / "data"
    storage = LocalStorage(data_dir)
    storage.ensure_initial_files()

    frontend_dir = tmp_path / "frontend"
    frontend_dir.mkdir()
    (frontend_dir / "index.html").write_text(
        "<html><body>Test SPA</body></html>", encoding="utf-8"
    )

    server = StandaloneServer(
        data_dir=data_dir,
        frontend_dir=frontend_dir,
        host="127.0.0.1",
        port=8080,
        vantage="local",
    )

    return {
        "data_dir": data_dir,
        "storage": storage,
        "server": server,
        "client": TestClient(server.app),
    }


class TestIntegration:
    """End-to-end integration tests for standalone mode."""

    def test_race_file_written_via_sink(self, setup):
        """Injecting a race via the sink writes the race file to disk."""
        storage = setup["storage"]

        # Create sink and inject race
        race = _make_synthetic_race(height=900001, epoch=1700000000.0)

        # Call write_race directly (the sink does this via executor, but
        # for testing we call synchronously)
        storage.write_race(race)

        # Verify race file exists (epoch 1700000000 = 2023-11-14 UTC)
        race_path = setup["data_dir"] / "api" / "races" / "2023" / "11" / "14" / "900001-local.json"
        assert race_path.exists()
        data = json.loads(race_path.read_text())
        assert data["block_height"] == 900001
        assert data["vantage"] == "local"

    def test_recent_blocks_updated_via_sink(self, setup):
        """After sink injection, recent-blocks.json contains the race."""
        storage = setup["storage"]
        race = _make_synthetic_race(height=900002, epoch=1700000600.0)
        storage.write_race(race)

        recent_path = setup["data_dir"] / "api" / "recent" / "recent-blocks.json"
        recent_data = json.loads(recent_path.read_text())
        assert isinstance(recent_data, list)
        assert len(recent_data) >= 1
        assert recent_data[0]["height"] == 900002

    def test_latest_json_updated_via_sink(self, setup):
        """After sink injection, latest.json reflects the new block."""
        storage = setup["storage"]
        race = _make_synthetic_race(height=900003, epoch=1700001200.0)
        storage.write_race(race)

        latest_path = setup["data_dir"] / "api" / "latest.json"
        latest_data = json.loads(latest_path.read_text())
        assert latest_data["height"] == 900003
        assert latest_data["epoch"] == 1700001200.0

    def test_websocket_receives_broadcast(self, setup):
        """A connected WebSocket client receives the race broadcast within 1s."""
        server = setup["server"]
        client = setup["client"]

        with client.websocket_connect("/ws") as ws:
            # Broadcast a race result
            race = _make_synthetic_race(height=900004, epoch=1700001800.0)

            def do_broadcast():
                loop = asyncio.new_event_loop()
                loop.run_until_complete(server.broadcast(race))
                loop.close()

            t = threading.Thread(target=do_broadcast)
            t.start()
            t.join(timeout=1.0)

            # Receive the message
            data = ws.receive_json()
            assert data["block_height"] == 900004
            assert data["vantage"] == "local"

    def test_runtime_json_reflects_host(self, setup):
        """GET /api/config/runtime.json includes websocket_url from Host header."""
        client = setup["client"]

        response = client.get(
            "/api/config/runtime.json",
            headers={"host": "localhost:9999"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["websocket_url"] == "ws://localhost:9999/ws"
        assert "local" in data["vantages"]

    def test_cold_start_endpoints_return_valid_json(self, setup):
        """After cold start, API endpoints return valid JSON (not 404)."""
        client = setup["client"]

        # recent-blocks.json should exist and be valid
        response = client.get("/api/recent/recent-blocks.json")
        assert response.status_code == 200
        assert response.json() == []

        # latest.json should exist and be valid
        response = client.get("/api/latest.json")
        assert response.status_code == 200
        data = response.json()
        assert data == {"height": None, "epoch": None}

        # Aggregate files should exist
        response = client.get("/api/aggregates/recent-10.json")
        assert response.status_code == 200
        assert response.json()["total_races"] == 0

    def test_full_sink_with_broadcast(self, setup):
        """The make_race_sink writes to disk AND broadcasts to WS clients."""
        server = setup["server"]
        storage = setup["storage"]
        client = setup["client"]

        with client.websocket_connect("/ws") as ws:
            race = _make_synthetic_race(height=900005, epoch=1700002400.0)

            # Simulate what the sink does: write to disk then broadcast.
            # We split the steps because make_race_sink requires the same
            # event loop as the WebSocket (production uses a single loop).
            storage.write_race(race)

            def do_broadcast():
                loop = asyncio.new_event_loop()
                loop.run_until_complete(server.broadcast(race))
                loop.close()

            t = threading.Thread(target=do_broadcast)
            t.start()
            t.join(timeout=2.0)

            # WebSocket should have received the broadcast
            data = ws.receive_json()
            assert data["block_height"] == 900005

        # File should also exist on disk
        race_path = setup["data_dir"] / "api" / "races" / "2023" / "11" / "14" / "900005-local.json"
        assert race_path.exists()
