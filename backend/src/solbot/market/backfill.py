"""Resumable history reconciliation; cursors never substitute for gap checks."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any

from solbot.storage.models import INTERVAL_MS

from .rest import AccessBlocked, BinanceRest, MarketDataError

DAY_MS = 86_400_000


def history_start(now_ms: int) -> int:
    """Start of the previous 30 full UTC calendar days, including today after them."""
    return now_ms // DAY_MS * DAY_MS - 30 * DAY_MS


def detect_gaps(times: Iterable[int], start: int, end: int, step: int) -> list[tuple[int, int]]:
    """Find inclusive ranges of missing candle OPEN times, including interior holes."""
    if step <= 0:
        raise ValueError("step must be positive")
    first = ((start + step - 1) // step) * step
    last = end // step * step
    cursor = first
    gaps: list[tuple[int, int]] = []
    for value in sorted({value for value in times if first <= value <= last and value % step == 0}):
        if value > cursor:
            gaps.append((cursor, value - step))
        cursor = value + step
    if cursor <= last:
        gaps.append((cursor, last))
    return gaps


class Backfill:
    """Scan durable candles on every run and persist checkpoints after each page.

    ``start`` and ``end`` are inclusive UTC millisecond bounds. A final open
    candle is always excluded using the synchronized Binance server clock.
    Pages contain at most 1000 rows; no historical candle cache is retained.
    """

    def __init__(self, db: Any, rest: BinanceRest, *, repair_passes: int = 2) -> None:
        if not 1 <= repair_passes <= 5:
            raise ValueError("repair_passes must be between 1 and 5")
        self.db = db
        self.rest = rest
        if getattr(db, "symbol", None) and getattr(rest, "symbol", db.symbol) != db.symbol:
            raise ValueError("REST client and database symbols disagree")
        self.repair_passes = repair_passes
        self.errors: dict[str, str] = {}

    async def _gaps(self, interval: str, start: int, end: int) -> list[tuple[int, int]]:
        times = await asyncio.to_thread(self.db.open_times, interval, start, end)
        gaps = detect_gaps(times, start, end, INTERVAL_MS[interval])
        await self.db.replace_gaps(interval, gaps, start, end)
        return gaps

    async def _fill(self, interval: str, start: int, end: int) -> None:
        step = INTERVAL_MS[interval]
        cursor = start
        while cursor <= end:
            rows = await self.rest.klines(interval, cursor, end, limit=1000)
            # Drop duplicates, open candles, and out-of-request rows. Advance only
            # after durable writes; unexpected empty pages cannot spin forever.
            unique = {row.open_time: row for row in rows if row.is_closed and cursor <= row.open_time <= end}
            if not unique:
                raise MarketDataError(f"no progress filling {interval} at {cursor}")
            ordered = [unique[key] for key in sorted(unique)]
            await self.db.write_klines(ordered)
            cursor = ordered[-1].open_time + step
            await self.db.set_sync(interval, cursor, "syncing")

    async def run(self, start: int, end: int) -> dict[str, list[tuple[int, int]]]:
        if start < 0 or end < start:
            raise ValueError("invalid backfill bounds")
        now_ms = await self.rest.server_time()
        result: dict[str, list[tuple[int, int]]] = {}
        self.errors = {}
        for interval, step in INTERVAL_MS.items():
            first = ((start + step - 1) // step) * step
            last = min(end // step * step, now_ms // step * step - step)
            if first > last:
                await self.db.set_sync(interval, first, "complete")
                result[interval] = []
                continue
            gaps = await self._gaps(interval, first, last)
            try:
                for _ in range(self.repair_passes):
                    if not gaps:
                        break
                    for gap_start, gap_end in gaps:
                        await self._fill(interval, gap_start, gap_end)
                    gaps = await self._gaps(interval, first, last)
                # A closed WS candle may differ from the final REST value. Check
                # every unconfirmed row in the window, including pre-restart rows.
                # Storage logs conflicts and makes REST authoritative explicitly.
                pending = await asyncio.to_thread(self.db.unconfirmed_open_times, interval, first, last)
                if pending:
                    ordered = sorted(set(pending))
                    range_start = previous = ordered[0]
                    for value in ordered[1:]:
                        if value != previous + step:
                            await self._fill(interval, range_start, previous)
                            range_start = value
                        previous = value
                    await self._fill(interval, range_start, previous)
                    remaining = await asyncio.to_thread(self.db.unconfirmed_open_times, interval, first, last)
                    if set(pending).intersection(remaining):
                        raise MarketDataError(f"REST did not confirm every pending {interval} candle")
            except AccessBlocked:
                raise
            except (MarketDataError, ValueError, OSError) as exc:
                self.errors[interval] = str(exc)[:300]
                await self.db.event("backfill_error", {"interval": interval, "error": str(exc)[:300]})
            # Even after a partially successful page, publish the remaining holes.
            gaps = await self._gaps(interval, first, last)
            cursor = gaps[0][0] if gaps else last + step
            await self.db.set_sync(interval, cursor, "unavailable" if gaps or interval in self.errors else "complete")
            result[interval] = gaps
        return result

    async def repair(self, start: int, end: int) -> dict[str, list[tuple[int, int]]]:
        return await self.run(start, end)
