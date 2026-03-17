"""FastAPI application entry point."""

import logging
import logging.config
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.api.routes import router
from src.api.websocket import websocket_all_prices, websocket_prices, websocket_signals
from src.collectors.realtime_collector import start_realtime_streams
from src.config import settings
from src.database.engine import init_db
from src.database.seed import seed_instruments
from src.scheduler.jobs import start_scheduler, stop_scheduler

# ── Logging Setup ────────────────────────────────────────────────────────────

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
# Suppress noisy third-party loggers
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("yfinance").setLevel(logging.WARNING)
logging.getLogger("peewee").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


# ── Lifespan ──────────────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifecycle: startup and shutdown."""
    # Startup
    logger.info("Starting Market Analyzer Pro...")

    # Ensure data directory exists
    Path("data").mkdir(parents=True, exist_ok=True)

    # Initialize database
    await init_db()
    logger.info("Database initialized")

    # Seed initial instruments
    await seed_instruments()
    logger.info("Instruments seeded")

    # Start background scheduler
    start_scheduler()
    logger.info("Scheduler started")

    # Start real-time price streams (Binance + Finnhub WebSocket)
    await start_realtime_streams()
    logger.info("Real-time streams started")

    logger.info("Market Analyzer Pro is ready!")

    yield  # Application is running

    # Shutdown
    logger.info("Shutting down Market Analyzer Pro...")
    stop_scheduler()
    logger.info("Scheduler stopped")


# ── FastAPI App ──────────────────────────────────────────────────────────────


app = FastAPI(
    title="Market Analyzer Pro",
    description="Financial market analysis and trading signal generation platform",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# REST API routes
app.include_router(router)


# ── WebSocket Routes ──────────────────────────────────────────────────────────


@app.websocket("/ws/prices")
async def ws_all_prices(websocket: WebSocket) -> None:
    """WebSocket endpoint for all symbols price updates (sidebar)."""
    await websocket_all_prices(websocket)


@app.websocket("/ws/prices/{symbol}")
async def ws_prices(websocket: WebSocket, symbol: str) -> None:
    """WebSocket endpoint for real-time price updates."""
    await websocket_prices(websocket, symbol)


@app.websocket("/ws/signals")
async def ws_signals(websocket: WebSocket) -> None:
    """WebSocket endpoint for real-time signal updates."""
    await websocket_signals(websocket)


# ── Static Files (Frontend) ──────────────────────────────────────────────────


frontend_dir = Path(__file__).parent.parent / "frontend"
if frontend_dir.exists():
    app.mount("/assets", StaticFiles(directory=str(frontend_dir / "assets")), name="assets")

    @app.get("/")
    async def serve_dashboard() -> FileResponse:
        return FileResponse(str(frontend_dir / "index.html"))

    @app.get("/signals")
    async def serve_signals() -> FileResponse:
        return FileResponse(str(frontend_dir / "signals.html"))

    @app.get("/accuracy")
    async def serve_accuracy() -> FileResponse:
        return FileResponse(str(frontend_dir / "accuracy.html"))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "src.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=True,
        log_level=settings.LOG_LEVEL.lower(),
    )
