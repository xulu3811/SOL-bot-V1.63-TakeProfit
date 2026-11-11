"""Bounded market-data reads and dependency-aware health reporting."""

import asyncio
from collections.abc import Callable
import copy
import logging
import sqlite3
import time
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from solbot.storage.database import DatabaseUnavailable
from solbot.storage.models import INTERVAL_MS

logger = logging.getLogger(__name__)
router = APIRouter()
market = APIRouter(prefix="/api/v1/market")
Interval = Literal["1m", "5m", "15m", "1h", "4h"]
Symbol = Literal["SOLUSDT", "SUIUSDT"]
Limit = Annotated[int, Query(ge=1, le=1000)]
Timestamp = Annotated[
    int | None,
    Query(ge=1_000_000_000_000, lt=4_102_444_800_000, description="UTC milliseconds; inclusive candle open time"),
]


def configured_symbol(request: Request, symbol: str | None = None) -> str:
    configured = request.app.state.settings.symbol
    if symbol is not None and symbol != configured:
        raise HTTPException(status_code=422, detail="symbol does not match this isolated market service")
    return configured


async def read_database(request: Request, operation: Callable[[Any], Any]) -> Any:
    """Keep short-lived read connections and their busy timeout off the event loop."""
    if not request.app.state.database_available:
        raise HTTPException(status_code=503, detail="database unavailable")
    try:
        return await asyncio.to_thread(operation, request.app.state.database)
    except (DatabaseUnavailable, sqlite3.Error, OSError) as exc:
        logger.warning("Database read unavailable: %s", type(exc).__name__)
        raise HTTPException(status_code=503, detail="database unavailable") from exc


def health_report(request: Request, summary: dict[str, Any]) -> dict[str, Any]:
    now_ms = int(time.time() * 1000)
    settings = request.app.state.settings
    collector = request.app.state.collector
    state = copy.deepcopy(collector.health) if collector is not None else {}
    task = request.app.state.collector_task
    reasons: list[str] = []
    if task is None or task.done():
        reasons.append("collector_stopped")
    if state.get("status") == "unavailable":
        reasons.append("collector_unavailable")
    if state.get("market_status") == "BLOCKED":
        reasons.append("market_blocked")
    if not state.get("ws_connected", False):
        reasons.append("websocket_disconnected")
    last_message = state.get("last_message_ms")
    stale_ms = settings.stale_after_seconds * 1000
    if not isinstance(last_message, int) or now_ms - last_message > stale_ms:
        reasons.append("stream_stale")
    if not state.get("backfill_complete", False):
        reasons.append("backfill_incomplete")

    offset = state.get("clock_offset_ms", 0)
    exchange_now = now_ms + (offset if isinstance(offset, (int, float)) else 0)
    interval_reports: dict[str, Any] = {}
    missing_total = 0
    gap_total = 0
    for interval, step in INTERVAL_MS.items():
        stored = summary.get("intervals", {}).get(interval, {})
        last_open = stored.get("last_open_time")
        boundary = int(exchange_now // step) * step
        expected_open = boundary - step
        # Allow the exchange close event and SQLite commit to settle at a boundary.
        minimum_open = expected_open
        if exchange_now - boundary < settings.close_grace_seconds * 1000:
            minimum_open -= step
        persisted_stale = not isinstance(last_open, int) or last_open < minimum_open
        received_at = state.get("last_by_interval", {}).get(interval)
        stream_stale = not isinstance(received_at, int) or now_ms - received_at > stale_ms
        interval_state = state.get("interval_health", {}).get(interval, {})
        stream_stale = stream_stale or bool(interval_state.get("stale", False))
        gap_count = int(stored.get("gap_count", 0))
        missing_bars = int(stored.get("missing_bars", 0))
        gap_total += gap_count
        missing_total += missing_bars
        if persisted_stale:
            reasons.append(f"{interval}:closed_candles_stale")
        if stream_stale:
            reasons.append(f"{interval}:stream_stale")
        interval_reports[interval] = {
            "last_open_time": last_open,
            "last_close_time": stored.get("last_close_time"),
            "expected_latest_open_time": expected_open,
            "last_message_ms": received_at,
            "closed_candles_stale": persisted_stale,
            "stream_stale": stream_stale,
            "gap_count": gap_count,
            "missing_bars": missing_bars,
            "checked_start": stored.get("checked_start"),
            "checked_end": stored.get("checked_end"),
        }
    if gap_total or missing_total or state.get("history_gaps", 0):
        reasons.append("history_gaps")
    return {
        "status": "healthy" if not reasons else "degraded",
        "live": True,
        "ready": not reasons,
        "symbol": configured_symbol(request),
        "now_ms": now_ms,
        "database": "available",
        "reasons": reasons,
        "intervals": interval_reports,
        "gap_count": gap_total,
        "missing_bars": missing_total,
        "collector": state,
    }


@router.get("/health")
async def health(request: Request) -> JSONResponse:
    """Readiness: 503 includes actionable causes, even while the process is live."""
    try:
        summary = await read_database(request, lambda db: db.summary())
    except HTTPException as exc:
        if exc.status_code != 503:
            raise
        return JSONResponse(status_code=503, content={
            "status": "unavailable", "live": True, "ready": False,
            "symbol": configured_symbol(request),
            "database": "unavailable", "reasons": ["database_unavailable"],
        })
    report = health_report(request, summary)
    return JSONResponse(status_code=200 if report["ready"] else 503, content=report)


@market.get("/status")
async def status(request: Request, symbol: Symbol | None = None) -> dict[str, Any]:
    symbol = configured_symbol(request, symbol)
    summary = await read_database(request, lambda db: db.summary())
    return {"symbol": symbol, "health": health_report(request, summary), "storage": summary}


@market.get("/klines")
async def klines(
    request: Request,
    interval: Interval = "1m",
    symbol: Symbol | None = None,
    limit: Limit = 500,
    start: Timestamp = None,
    end: Timestamp = None,
) -> dict[str, Any]:
    symbol = configured_symbol(request, symbol)
    if start is not None and end is not None and start > end:
        raise HTTPException(status_code=422, detail="start must be less than or equal to end")

    def fetch_page(db: Any) -> tuple[list[dict[str, Any]], int | None]:
        rows = db.klines(interval, limit=limit, start=start, end=end)
        next_start = rows[-1]["open_time"] + INTERVAL_MS[interval] if rows else None
        if len(rows) < limit or next_start is None or (end is not None and next_start > end):
            return rows, None
        # Probe a single next record; every individual database read is bounded.
        has_more = db.klines(interval, limit=1, start=next_start, end=end)
        return rows, next_start if has_more else None

    rows, next_start = await read_database(request, fetch_page)
    return {
        "symbol": symbol, "interval": interval, "start": start, "end": end,
        "limit": limit, "count": len(rows), "items": rows, "next_start": next_start,
    }


@market.get("/latest")
async def latest(
    request: Request, interval: Interval = "1m", symbol: Symbol | None = None
) -> dict[str, Any]:
    symbol = configured_symbol(request, symbol)
    row = await read_database(request, lambda db: db.latest(interval))
    return {"symbol": symbol, "interval": interval, "available": row is not None, "kline": row}


@market.get("/gaps")
async def gaps(
    request: Request,
    interval: Interval | None = None,
    symbol: Symbol | None = None,
    limit: Limit = 500,
    offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
) -> dict[str, Any]:
    symbol = configured_symbol(request, symbol)
    rows = await read_database(
        request, lambda db: db.gaps(interval=interval, limit=limit + 1, offset=offset)
    )
    has_more = len(rows) > limit
    return {
        "symbol": symbol, "interval": interval, "limit": limit,
        "offset": offset, "count": min(len(rows), limit), "items": rows[:limit],
        "next_offset": offset + limit if has_more else None,
    }


router.include_router(market)
