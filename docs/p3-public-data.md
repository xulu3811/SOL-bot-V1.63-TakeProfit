# P3-A public microstructure interface

The worker imports no execution, private-account or credential provider. One independent process and one new SQLite database serve one configured symbol, SOLUSDT or SUIUSDT. Existing P1/P2 collectors and their files are unchanged.

Run `python -m solbot.market.depth_collector --symbol SOLUSDT --database /var/lib/sol-bot/database/sol-micro.sqlite --status-path /var/lib/sol-bot/status/sol-micro.json`. Use a different database/status file for SUIUSDT. No HTTP endpoint is added. The status path must be a separate `.json` file; database ownership is verified before initialization writes.

## Source synchronization

The combined official public stream carries diff-depth, aggTrade, and bookTicker. Depth starts buffering before a 500-level REST snapshot. A stale snapshot is retried at most three times; older events are ignored, a bridging update establishes synchronization, and missing update IDs reset the book. Zero quantities delete levels. [Binance stream specification](https://raw.githubusercontent.com/binance/binance-spot-api-docs/master/web-socket-streams.md)

The snapshot uses `/api/v3/depth` with limit 500 and its documented weight 25. Existing public client timeout, pacing, nonredirect, credential-free and bounded retry conventions are reused. A 418/451 response stops further requests until operator action; no origin/proxy fallback is used. [Binance public market endpoints](https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/market)

Top-20 output is restricted to known snapshot coverage. If updates move the visible top 20 beyond that coverage, if either side empties/crosses, or if a memory bound would be exceeded, the worker resets and resynchronizes instead of presenting incomplete depth as complete. A snapshot alone has `synced=false`.

## Public Python APIs

- `OrderBook(symbol, max_levels=2048)`: `load_snapshot(payload, received_ms)`, `apply(event, received_ms) -> bool`, `levels(n=20) -> (bids, asks)`, `reset()`. Levels are Decimal `(price, quantity)` pairs. Sequence/coverage loss raises `SequenceGap` and clears synchronization.
- `TradeWindow(symbol, window_ms=60000, max_trades=20000)`: `add(agg_trade_payload, received_ms) -> bool`, `features(exchange_now_ms) -> dict`, `reset()`. Duplicate aggregate IDs do not contribute twice. ID gaps clear the window and require a new full window; regressing trade/exchange times are rejected.
- `BookTicker(symbol).add(payload, received_ms) -> bool`: absent exchange event time remains null. Local receipt time is stored separately.
- `microprice(bid, ask, bid_qty, ask_qty)`, `spread_metrics(bid, ask)`, and `depth_imbalance(bids, asks)` validate inputs and calculate with Decimal precision 40.
- `MicrostructureStore(path, symbol, read_only=False, retention_days=7, max_rows=120000, history_interval_ms=5000)`: `append(snapshot)`, `latest() -> dict | None`, `history(limit=100)`. Read consumers should set `read_only=True`. No async start/close call is required; connections are short-lived. Writes are serialized and transactional.
- `DepthCollector(store, symbol=None, status_path=None, ...)`: `run()` runs until cancellation; `snapshot(now_ms=None)` returns the contract fields. REST factory and WebSocket connector are injectable for offline tests.

The JSON snapshot includes every field listed in `docs/p3-contract.md`; all monetary/ratio/statistic values are decimal strings. Source/event timestamps and local receipt timestamps are distinct. `input_hash` is SHA-256 over canonical sorted compact JSON excluding only the hash field itself. `feature_generated_time_ms` is the publication time; it is not substituted for exchange or quote receipt time.

## Definitions and readiness

Spread is ask minus bid; spread basis points divide by the arithmetic midpoint. Microprice weights ask by best-bid quantity and bid by best-ask quantity. Depth imbalance uses total base quantity over the exported top 20: `(bid_qty_sum - ask_qty_sum) / (bid_qty_sum + ask_qty_sum)`.

The trade window uses the latest 60 seconds of actual trade timestamps. `buy_ratio` is aggressive-buy quote notional divided by all quote notional; the buyer is aggressive when the exchange reports buyer-maker=false. `trade_volume` is base quantity, `trade_count` counts aggregate-trade events, and `raw_trade_count` sums constituent trade-ID ranges. `short_volatility` is the population standard deviation of consecutive simple trade-price returns, with Decimal square root; it is neither annualized nor an ATR substitute. It is null with insufficient prices. `quote_updates_per_sec` counts accepted bookTicker changes over 60 seconds divided by 60.

After startup/reconnection, a full 60-second trade observation window is required. Sequence gaps or capacity loss make that window incomplete. Readiness also requires synchronized sufficient depth, active verified Spot rules, a connected stream, depth/bookTicker receipt freshness within five seconds, and trade source/receipt freshness within 15 seconds. Exchange clock offset is estimated from the official server clock. If bookTicker supplies an event timestamp it is validated; absent timestamps stay null.

The consumer must independently reject an old persisted snapshot using source and receipt timestamps. `latest()` returns the original hash-verifiable stored record, so an old `ready=true` flag is not a perpetual liveness guarantee. The root mathematical runtime owns this consumer check and separately archives complete referenced inputs in immutable decision audit records.

## Bounds and evidence limits

Each depth side is capped at 2,048 levels, the bootstrap/consumer depth queue at 1,024 events, the library receive queue at 64, each WebSocket message at 256 KiB, trades at 20,000 events, quote timestamps at 20,000, and recent error diagnostics at 20. Capacity loss is explicit rather than silently treated as a complete measurement window.

The latest feature record is updated once per second by default. Research history is sampled every five seconds and retained for at most seven days or 120,000 records, whichever limit is reached first. Each encoded record is at most 64 KiB. This research pruning never applies to strategy decisions, execution idempotency, account state, or root-owned permanent input audits; those live in separate files. No historical depth is reconstructed or invented for periods before collection.

Targeted tests use local fakes and temporary databases, including independent Fraction/Decimal reference calculations, sequence and coverage loss, reconnect paths, REST retries/access blocks, symbol isolation, database misuse, time regressions, sampling/retention, and hash checks. They do not themselves establish production resource usage, long-term stream availability, or reliable fill probabilities. Actual deployment and observation evidence is recorded by the parent task.
