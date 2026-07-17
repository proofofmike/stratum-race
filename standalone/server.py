"""Standalone HTTP + WebSocket server for StratumRace.

Serves the Vue SPA, JSON API files from the data directory, provides
a WebSocket endpoint for real-time race broadcasts, and generates
dynamic runtime configuration.

Run via uvicorn programmatically so the HTTP server, collector, and
aggregator share one asyncio event loop.
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Set

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)


class StandaloneServer:
    """The standalone HTTP + WebSocket server.

    Attributes:
        data_dir: Path to the data directory (contains api/ subtree)
        frontend_dir: Path to the built frontend (dist/) directory
        host: Bind address
        port: Bind port
        vantage: Vantage label for runtime.json
        ws_clients: Set of connected WebSocket instances
    """

    def __init__(
        self,
        data_dir: Path,
        frontend_dir: Path,
        host: str = "0.0.0.0",
        port: int = 8080,
        vantage: str = "local",
    ):
        if not (1 <= port <= 65535):
            raise ValueError(f"Port must be between 1 and 65535, got {port}")

        self.data_dir = Path(data_dir)
        self.frontend_dir = Path(frontend_dir)
        self.host = host
        self.port = port
        self.vantage = vantage
        self.ws_clients: Set[WebSocket] = set()
        self._app = self._build_app()

    def _build_app(self) -> Starlette:
        """Build the Starlette application with all routes."""
        routes = [
            Route("/healthz", endpoint=self._healthz, methods=["GET"]),
            Route(
                "/api/config/runtime.json",
                endpoint=self._runtime_config,
                methods=["GET"],
            ),
            Route("/api/{path:path}", endpoint=self._api_file, methods=["GET"]),
            WebSocketRoute("/ws", endpoint=self._websocket_handler),
        ]

        # Mount static frontend files (html=True serves index.html for
        # directory paths)
        if self.frontend_dir.exists():
            routes.append(
                Mount(
                    "/",
                    app=StaticFiles(directory=str(self.frontend_dir), html=True),
                    name="static",
                )
            )

        app = Starlette(routes=routes)

        # SPA fallback: any request that doesn't match a defined route or
        # a static file returns index.html with 200. This is implemented
        # as a custom exception handler for 404 responses.
        if self.frontend_dir.exists():
            index_path = self.frontend_dir / "index.html"

            async def spa_fallback(request: Request, exc: Exception) -> Response:
                """Serve index.html for unmatched paths (SPA client-side routing)."""
                # Only fall back for non-API paths
                if request.url.path.startswith("/api/"):
                    return JSONResponse(
                        {"error": "Not found"}, status_code=404
                    )
                if index_path.is_file():
                    return FileResponse(
                        str(index_path), media_type="text/html", status_code=200
                    )
                return Response("Not Found", status_code=404)

            app.add_exception_handler(404, spa_fallback)

        return app

    # ------------------------------------------------------------------
    # Route handlers
    # ------------------------------------------------------------------

    async def _healthz(self, request: Request) -> JSONResponse:
        """Health check endpoint."""
        return JSONResponse({"status": "ok"})

    async def _runtime_config(self, request: Request) -> JSONResponse:
        """Generate runtime.json dynamically from the request Host header."""
        host_header = request.headers.get("host", f"localhost:{self.port}")
        # Build WebSocket URL from the host header
        ws_url = f"ws://{host_header}/ws"

        config = {
            "websocket_url": ws_url,
            "vantages": {self.vantage: {"label": self.vantage}},
        }
        return JSONResponse(config)

    async def _api_file(self, request: Request) -> Response:
        """Serve files from the data directory's api/ subtree."""
        path = request.path_params["path"]
        file_path = self.data_dir / "api" / path

        # Prevent path traversal
        try:
            file_path = file_path.resolve()
            api_root = (self.data_dir / "api").resolve()
            if not str(file_path).startswith(str(api_root)):
                return JSONResponse({"error": "Not found"}, status_code=404)
        except (OSError, ValueError):
            return JSONResponse({"error": "Not found"}, status_code=404)

        if not file_path.is_file():
            return JSONResponse({"error": "Not found"}, status_code=404)

        content_type = (
            "application/json"
            if file_path.suffix == ".json"
            else "application/octet-stream"
        )
        return FileResponse(str(file_path), media_type=content_type)

    async def _websocket_handler(self, websocket: WebSocket) -> None:
        """Accept WebSocket connections without auth, add to client set."""
        await websocket.accept()
        self.ws_clients.add(websocket)
        try:
            # Keep the connection alive — wait for client disconnect.
            # We don't expect messages from clients, but we must await
            # to detect disconnect.
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            self.ws_clients.discard(websocket)

    # ------------------------------------------------------------------
    # Broadcast
    # ------------------------------------------------------------------

    async def broadcast(self, message: dict) -> None:
        """Broadcast a JSON message to all connected WebSocket clients.

        Removes clients that fail to receive (broken pipe, etc).
        """
        if not self.ws_clients:
            return

        data = json.dumps(message)
        dead_clients: Set[WebSocket] = set()

        for client in self.ws_clients.copy():
            try:
                await client.send_text(data)
            except Exception:
                dead_clients.add(client)

        # Remove failed clients
        self.ws_clients -= dead_clients

    # ------------------------------------------------------------------
    # Async sink (for collector integration)
    # ------------------------------------------------------------------

    def make_race_sink(self, storage, aggregation_trigger=None):
        """Create an async race sink that writes to disk and broadcasts.

        Args:
            storage: LocalStorage instance for persisting race results
            aggregation_trigger: Optional asyncio.Event to signal the aggregator

        Returns:
            An async callable that the collector calls with each race_result dict.
        """
        loop = asyncio.get_running_loop()

        async def sink(race_result: dict) -> None:
            # Write to disk in executor (blocking I/O)
            await loop.run_in_executor(None, storage.write_race, race_result)
            # Broadcast to WebSocket clients
            await self.broadcast(race_result)
            # Signal the aggregator to run soon
            if aggregation_trigger is not None:
                aggregation_trigger.set()

        return sink

    # ------------------------------------------------------------------
    # Server lifecycle
    # ------------------------------------------------------------------

    @property
    def app(self) -> Starlette:
        """The ASGI application."""
        return self._app

    async def serve(self) -> None:
        """Start the server using uvicorn programmatically."""
        import uvicorn

        config = uvicorn.Config(
            app=self._app,
            host=self.host,
            port=self.port,
            log_level="info",
            access_log=False,
            ws="websockets",
        )
        server = uvicorn.Server(config)
        await server.serve()
