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
from src.api.routes_v2 import router_v2
from src.api.websocket import websocket_all_prices, websocket_prices, websocket_signals
from src.api.websocket_v2 import (
    portfolio_ws_handler,
    prices_ws_handler,
    signals_ws_handler,
)
from src.collectors.realtime_collector import start_realtime_streams
from src.config import settings
from src.database.engine import init_db
from src.database.seed import seed_instruments
from src.monitoring.setup import setup_metrics

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

    # Start real-time price streams (Binance + Finnhub WebSocket)
    await start_realtime_streams()
    logger.info("Real-time streams started")

    # Start APScheduler (price collection, signal generation, trade tracking)
    from src.scheduler.jobs import start_scheduler, scheduler
    start_scheduler()
    logger.info("Scheduler started")

    logger.info("Market Analyzer Pro is ready!")

    yield  # Application is running

    # Shutdown
    logger.info("Shutting down Market Analyzer Pro...")
    scheduler.shutdown(wait=False)
    logger.info("Scheduler stopped")


# ── FastAPI App ──────────────────────────────────────────────────────────────


app = FastAPI(
    title="Market Analyzer Pro",
    description=(
        "## Market Analyzer Pro v2 — Trading Signal Generation Platform\n\n"
        "Combines technical, fundamental, macro, and geopolitical analysis to generate\n"
        "regime-aware trading signals across Forex, US/EU Stocks, and Cryptocurrencies.\n\n"
        "### Key Features\n"
        "- Regime-adaptive composite scoring (TA/FA/Sentiment/Geo/OF)\n"
        "- Intelligent SL/TP with S&R alignment (RiskManagerV2)\n"
        "- Portfolio heat monitoring (max 6% total risk)\n"
        "- LLM-based signal validation (Claude)\n"
        "- Real-time WebSocket streams (signals, prices, portfolio)\n\n"
        "### Authentication\n"
        "API key authentication via `X-API-Key` header (when configured).\n\n"
        "### Rate Limits\n"
        "External API calls are protected by circuit breakers and backoff logic."
    ),
    version="2.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    contact={
        "name": "Market Analyzer Pro",
    },
    license_info={
        "name": "Proprietary",
    },
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Prometheus metrics
setup_metrics(app)

# REST API routes
app.include_router(router)
app.include_router(router_v2)


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


# ── WebSocket v2 Routes ───────────────────────────────────────────────────────


@app.websocket("/ws/v2/signals")
async def ws_v2_signals(websocket: WebSocket) -> None:
    """WebSocket v2: real-time signal alerts with full v2 schema."""
    await signals_ws_handler(websocket)


@app.websocket("/ws/v2/prices/{symbol}")
async def ws_v2_prices(websocket: WebSocket, symbol: str) -> None:
    """WebSocket v2: real-time price ticks for a specific symbol."""
    await prices_ws_handler(websocket, symbol)


@app.websocket("/ws/v2/portfolio")
async def ws_v2_portfolio(websocket: WebSocket) -> None:
    """WebSocket v2: real-time virtual portfolio updates."""
    await portfolio_ws_handler(websocket)


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
