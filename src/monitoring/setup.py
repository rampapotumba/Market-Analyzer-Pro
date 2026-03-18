"""
Prometheus instrumentation setup for FastAPI.

Call setup_metrics(app) once during application startup.
Exposes /metrics endpoint for Prometheus scraping.
"""

import logging

from fastapi import FastAPI

logger = logging.getLogger(__name__)


def setup_metrics(app: FastAPI) -> None:
    """
    Attach prometheus-fastapi-instrumentator to the FastAPI app.

    Exposes a /metrics endpoint that Prometheus can scrape.
    Default metrics include:
      - http_requests_total (method, handler, status)
      - http_request_duration_seconds (histogram)
      - http_request_size_bytes (histogram)
      - http_response_size_bytes (histogram)
    """
    try:
        from prometheus_fastapi_instrumentator import Instrumentator

        instrumentator = Instrumentator(
            should_group_status_codes=False,
            should_ignore_untemplated=True,
            should_group_untemplated=True,
            excluded_handlers=["/metrics", "/health", "/docs", "/openapi.json"],
            body_handlers=[],
        )
        instrumentator.instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
        logger.info("[Metrics] Prometheus /metrics endpoint enabled")

    except ImportError:
        logger.warning(
            "[Metrics] prometheus-fastapi-instrumentator not installed; "
            "/metrics endpoint unavailable"
        )
    except Exception as exc:
        logger.error("[Metrics] Failed to set up Prometheus instrumentation: %s", exc)
