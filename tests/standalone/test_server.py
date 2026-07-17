"""Tests for the standalone HTTP + WebSocket server.

Validates route behaviors, runtime.json host reflection, SPA fallback,
no-auth acceptance, and WebSocket connection handling.

Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 3.8, 3.10,
              4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 12.1, 12.2, 12.4, 12.5
"""

import json
import tempfile
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from standalone.server import StandaloneServer


@pytest.fixture
def data_dir(tmp_path):
    """Create a temporary data directory with api/ structure."""
    api_dir = tmp_path / "api"
    api_dir.mkdir()
    (api_dir / "config").mkdir(parents=True)
    (api_dir / "races").mkdir()
    (api_dir / "recent").mkdir()
    return tmp_path


@pytest.fixture
def frontend_dir(tmp_path):
    """Create a temporary frontend directory with an index.html."""
    fe_dir = tmp_path / "frontend"
    fe_dir.mkdir()
    (fe_dir / "index.html").write_text(
        "<html><body>StratumRace</body></html>", encoding="utf-8"
    )
    (fe_dir / "assets").mkdir()
    (fe_dir / "assets" / "main.js").write_text(
        "console.log('app');", encoding="utf-8"
    )
    return fe_dir


@pytest.fixture
def server(data_dir, frontend_dir):
    """Create a StandaloneServer instance for testing."""
    return StandaloneServer(
        data_dir=data_dir,
        frontend_dir=frontend_dir,
        host="127.0.0.1",
        port=8080,
        vantage="local",
    )


@pytest.fixture
def client(server):
    """Create a test client for the server's Starlette app."""
    return TestClient(server.app)


# ------------------------------------------------------------------
# Health check (R3.10)
# ------------------------------------------------------------------


class TestHealthz:
    def test_healthz_returns_200_ok(self, client):
        """GET /healthz returns 200 with status ok."""
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


# ------------------------------------------------------------------
# Runtime config (R3.7)
# ------------------------------------------------------------------


class TestRuntimeConfig:
    def test_runtime_json_reflects_host_header(self, client):
        """runtime.json websocket_url uses the request Host header."""
        response = client.get(
            "/api/config/runtime.json",
            headers={"host": "myhost.local:9090"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["websocket_url"] == "ws://myhost.local:9090/ws"

    def test_runtime_json_contains_vantage(self, client):
        """runtime.json contains the configured vantage in the vantages map."""
        response = client.get("/api/config/runtime.json")
        assert response.status_code == 200
        data = response.json()
        assert "vantages" in data
        assert "local" in data["vantages"]
        assert data["vantages"]["local"]["label"] == "local"

    def test_runtime_json_custom_vantage(self, data_dir, frontend_dir):
        """runtime.json uses a custom vantage label when configured."""
        srv = StandaloneServer(
            data_dir=data_dir,
            frontend_dir=frontend_dir,
            vantage="my-node",
        )
        test_client = TestClient(srv.app)
        response = test_client.get("/api/config/runtime.json")
        data = response.json()
        assert "my-node" in data["vantages"]
        assert data["vantages"]["my-node"]["label"] == "my-node"


# ------------------------------------------------------------------
# API file serving (R3.2, R3.8)
# ------------------------------------------------------------------


class TestApiFileServing:
    def test_api_file_serves_json(self, client, data_dir):
        """Serve a JSON file from the api/ subtree with correct content type."""
        # Create a test JSON file
        races_dir = data_dir / "api" / "races"
        test_file = races_dir / "test.json"
        test_file.write_text('{"height": 800000}', encoding="utf-8")

        response = client.get("/api/races/test.json")
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/json"
        assert response.json() == {"height": 800000}

    def test_api_file_404_for_missing(self, client):
        """Return 404 for non-existent API file."""
        response = client.get("/api/does/not/exist.json")
        assert response.status_code == 404
        assert response.json()["error"] == "Not found"

    def test_api_file_serves_nested_path(self, client, data_dir):
        """Serve files from nested subdirectories."""
        nested_dir = data_dir / "api" / "races" / "2024" / "01" / "15"
        nested_dir.mkdir(parents=True)
        race_file = nested_dir / "800000-local.json"
        race_data = {"block_height": 800000, "vantage": "local"}
        race_file.write_text(json.dumps(race_data), encoding="utf-8")

        response = client.get("/api/races/2024/01/15/800000-local.json")
        assert response.status_code == 200
        assert response.json() == race_data

    def test_api_pools_config_served(self, client, data_dir):
        """Serve pools.json from api/config/ path (R3.6)."""
        pools = [{"name": "pool1", "host": "pool1.example.com", "port": 3333}]
        config_dir = data_dir / "api" / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "pools.json").write_text(
            json.dumps(pools), encoding="utf-8"
        )

        response = client.get("/api/config/pools.json")
        assert response.status_code == 200
        assert response.json() == pools


# ------------------------------------------------------------------
# SPA fallback (R3.1, R3.3)
# ------------------------------------------------------------------


class TestSpaFallback:
    def test_spa_serves_index_html_at_root(self, client):
        """Root path serves the SPA index.html."""
        response = client.get("/")
        assert response.status_code == 200
        assert "StratumRace" in response.text

    def test_spa_fallback_returns_index_html(self, client):
        """Unknown paths fall back to index.html with 200 status."""
        response = client.get("/some/unknown/route")
        assert response.status_code == 200
        assert "StratumRace" in response.text

    def test_spa_serves_static_asset(self, client):
        """Known static assets are served directly."""
        response = client.get("/assets/main.js")
        assert response.status_code == 200
        assert "console.log" in response.text


# ------------------------------------------------------------------
# No auth (R12.1, R12.2, R12.5)
# ------------------------------------------------------------------


class TestNoAuth:
    def test_no_auth_required(self, client):
        """Requests with auth headers are processed normally (R12.5)."""
        response = client.get(
            "/healthz",
            headers={
                "Authorization": "Bearer fake-token",
                "X-API-Key": "some-key",
            },
        )
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_no_auth_on_api(self, client, data_dir):
        """API endpoints work without auth."""
        test_file = data_dir / "api" / "recent" / "recent-blocks.json"
        test_file.write_text("[]", encoding="utf-8")

        response = client.get("/api/recent/recent-blocks.json")
        assert response.status_code == 200

    def test_read_only_surface(self, client):
        """POST/PUT/DELETE are not allowed on API routes (R12.4)."""
        response = client.post("/api/config/runtime.json")
        assert response.status_code == 405

        response = client.put("/healthz")
        assert response.status_code == 405

        response = client.delete("/api/races/test.json")
        assert response.status_code == 405


# ------------------------------------------------------------------
# WebSocket (R4.1, R4.3, R4.4)
# ------------------------------------------------------------------


class TestWebSocket:
    def test_websocket_accepts_connection(self, client, server):
        """WebSocket connects without auth (R4.1, R4.3, R12.2)."""
        with client.websocket_connect("/ws") as ws:
            # Connection accepted — client is in the set
            assert len(server.ws_clients) == 1

        # After disconnect, client is removed
        assert len(server.ws_clients) == 0

    def test_websocket_broadcast(self, client, server):
        """Broadcast delivers messages to connected clients (R4.2)."""
        import asyncio

        with client.websocket_connect("/ws") as ws:
            # Trigger a broadcast
            message = {"block_height": 800001, "vantage": "local"}

            # Use the server's broadcast — run in the test event loop
            import threading

            def do_broadcast():
                loop = asyncio.new_event_loop()
                loop.run_until_complete(server.broadcast(message))
                loop.close()

            t = threading.Thread(target=do_broadcast)
            t.start()
            t.join()

            data = ws.receive_json()
            assert data["block_height"] == 800001
            assert data["vantage"] == "local"

    def test_websocket_multiple_clients(self, server):
        """Multiple clients can connect simultaneously (R4.5)."""
        test_client = TestClient(server.app)

        # Connect two clients
        with test_client.websocket_connect("/ws") as ws1:
            with test_client.websocket_connect("/ws") as ws2:
                assert len(server.ws_clients) == 2

        assert len(server.ws_clients) == 0


# ------------------------------------------------------------------
# Port validation (R3.4, R3.5)
# ------------------------------------------------------------------


class TestPortValidation:
    def test_valid_port_range(self, data_dir, frontend_dir):
        """Valid ports within 1-65535 are accepted."""
        srv = StandaloneServer(
            data_dir=data_dir, frontend_dir=frontend_dir, port=1
        )
        assert srv.port == 1

        srv = StandaloneServer(
            data_dir=data_dir, frontend_dir=frontend_dir, port=65535
        )
        assert srv.port == 65535

    def test_invalid_port_zero(self, data_dir, frontend_dir):
        """Port 0 raises ValueError."""
        with pytest.raises(ValueError, match="Port must be between 1 and 65535"):
            StandaloneServer(
                data_dir=data_dir, frontend_dir=frontend_dir, port=0
            )

    def test_invalid_port_over_max(self, data_dir, frontend_dir):
        """Port above 65535 raises ValueError."""
        with pytest.raises(ValueError, match="Port must be between 1 and 65535"):
            StandaloneServer(
                data_dir=data_dir, frontend_dir=frontend_dir, port=70000
            )

    def test_configurable_host(self, data_dir, frontend_dir):
        """Host is configurable (R3.5)."""
        srv = StandaloneServer(
            data_dir=data_dir,
            frontend_dir=frontend_dir,
            host="127.0.0.1",
        )
        assert srv.host == "127.0.0.1"
