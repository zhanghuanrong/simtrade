"""FastAPI application factory for SimTrade server."""

from contextlib import asynccontextmanager
import os
from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
import logging

from simtrade.config import ServerConfig, SimulationConfig, MatchingConfig, MarginConfig
from simtrade.engine.simulator import Simulator
from simtrade.server import routes_api, routes_ws

logger = logging.getLogger(__name__)


def create_app(
    server_config: ServerConfig = ServerConfig(),
    sim_config: SimulationConfig = SimulationConfig(),
    matching_config: MatchingConfig = MatchingConfig(),
    margin_config: MarginConfig = MarginConfig(),
) -> FastAPI:
    """Create and configure the SimTrade FastAPI application."""
    
    # Initialize Simulator
    sim = Simulator(
        sim_config=sim_config,
        matching_config=matching_config,
        margin_config=margin_config,
    )
    routes_api.simulator = sim
    sim.register_broadcast_callback(routes_ws.broadcast_ws)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info(f"Starting SimTrade server on {server_config.host}:{server_config.port}")
        # Automatically step once to populate initial prices
        await sim.step()
        yield
        logger.info("Stopping SimTrade server...")
        sim.stop()

    app = FastAPI(
        title=server_config.title,
        version=server_config.version,
        lifespan=lifespan,
    )

    # Enable CORS for web clients
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Attach REST & WebSocket routers
    app.include_router(routes_api.api_router)
    app.include_router(routes_ws.ws_router)

    ui_file = Path(__file__).parent / "ui" / "index.html"

    @app.get("/", response_class=HTMLResponse, tags=["Dashboard"])
    @app.get("/dashboard", response_class=HTMLResponse, tags=["Dashboard"])
    async def serve_dashboard():
        if ui_file.exists():
            return FileResponse(
                ui_file,
                headers={
                    "Cache-Control": "no-cache, no-store, must-revalidate",
                    "Pragma": "no-cache",
                    "Expires": "0",
                },
            )
        return HTMLResponse("<h1>SimTrade Dashboard UI not found</h1>")

    return app
