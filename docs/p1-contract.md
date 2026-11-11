# P1 implementation contracts

Public market data only. Fixed SOLUSDT and intervals 1m,5m,15m,1h,4h.
Python >=3.11 (production 3.14). Package backend/src/solbot.

Storage interface owned by storage agent:
- storage.models.INTERVAL_MS dict, Kline dataclass with specified task fields, from_rest(row, interval, now_ms), from_ws(payload), validate(), asdict via dataclasses. Prices/volumes strings, UTC milliseconds, source rest/ws, is_closed bool. Reject bad units, alignment, OHLC and nonfinite values.
- storage.database.Database(path): async start(), close(), write_klines(list[Kline])->int; reads synchronous via read-only short connections: klines(interval,limit=500,start=None,end=None), latest(interval), summary(), gaps(); async replace_gaps(interval,list[tuple[start_ms,end_ms]], checked_start, checked_end), event(kind,detail), snapshot(dict), set_sync(interval,cursor,status); all writes one asyncio lock, DB operations off event loop if feasible. open_times(interval,start,end)->list[int]. backup(destination) async SQLite backup. Explicit migration, WAL, busy timeout, bounded retention metadata. No unfinished rows. Confirmed conflicts detect and report, REST authoritative reconcile by explicit policy.

Market interface owned by collector agent:
- BinanceRest async context manager, request(endpoint,params=None), server_time(), exchange_info(), book_ticker(), klines(interval,start,end,limit=1000). Fixed official origin and endpoint allowlist. Weight pacing, timeouts, bounded retries 429/5xx, respect Retry-After and never evade 418/451.
- Collector(db): async run(), stop via cancellation; .health dict JSON-safe containing ws_connected,last_message_ms,last_by_interval,reconnects,backfill_complete,errors etc. Collect five streams, cache bounded to five open klines, close-only persistent writes, reconnect and periodic gap repair. History previous 30 full UTC calendar days + today completed candles. REST server clock. Helpers detect_gaps(times,start,end,step), Backfill(db,rest).run(start,end) and repair. Tests local fakes.

API/app owner coordinates service startup using Database and Collector. Health combines per-interval freshness, history gaps, collector state; API pagination limit <=1000; startup schedules collection without blocking liveness. DB unavailable yields explicit 503. Single uvicorn worker bound 127.0.0.1 only. SOLBOT_DB_PATH environment config. No trading modules.
