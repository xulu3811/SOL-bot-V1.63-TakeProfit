# P3 implementation coordination

Authority: user attachment `ef5f11fd-1fc5-456b-8922-96ed93fe9b1f/Pasted text.txt`.
Read P2 required contracts/reports. Preserve 118 regression tests; no live clients,
secrets, private calls, or real orders. No new dependencies initially. Use Decimal
for all P3 money/prices/fees and persist parameters, input hashes and results.

## Ownership

- Public data worker owns `market/depth_collector.py`, `market/trade_collector.py`,
  `mathematics/orderbook.py`, `mathematics/microstructure.py`,
  `mathematics/market_features.py`, `storage/microstructure.py`, and its P3 tests.
- Pricing worker owns `mathematics/entry_pricing.py`, `exit_pricing.py`,
  `fill_probability.py`, `execution_cost.py`, `expected_return.py`,
  `position_sizing.py`, plus `mathematics/contracts.py`, `mathematics/decimal_utils.py`
  and corresponding tests. Publish interfaces early.
- Root owns APTS/protective math, shadow extension, integration runtime/deployment
  and reports. Root creates mathematics/__init__.py; do not edit it concurrently.
- Independent QA owns independent reference tests/review report, no implementation.

## Public features integration

Publish a JSON-safe feature snapshot from an independent process per symbol.
Required fields: `symbol`, `ready`, `synced`, `reasons`, `exchange_time_ms` (depth),
`received_time_ms`, `update_id`, `bid`, `ask`, `bid_qty`, `ask_qty`, `bids`, `asks`
(top20 arrays of decimal strings), `microprice`, `spread`, `spread_bps`,
`imbalance`, `buy_ratio` (nullable), `trade_volume`, `trade_count`,
`trade_event_time_ms`, `trade_received_time_ms`, `quote_updates_per_sec`,
`short_volatility`, `book_ticker_exchange_time_ms` (null if absent),
`book_ticker_received_time_ms`, `rules` (raw symbol exchangeInfo), `input_hash`.
Times are UTC milliseconds. Indicators based on closed Klines are supplied by
root separately; never use future bars. Root reads latest persisted micro snapshot.
Public worker exposes MicrostructureStore.latest() and owns a CLI with --symbol,
--database, --status-path. Document actual signatures. No public HTTP endpoint.
Depth REST/WS are official allowlisted public paths with bounded retries, queues,
history and memory. Retain research snapshots with explicit bounds; preserve all
decision referenced inputs in separate immutable audit records, not prunable data.

## Math interfaces

Pricing worker defines common immutable types and accepts feature dict above.
Every output includes symbol/account/strategy/version/signal/time/book-time/hash,
computed prices and quantities, fee/risk estimates, validity and reasons.
Public exports are documented by worker. No math result is Guardian ALLOW;
live_authorized must always be false. Missing empirical win/cost evidence yields
INSUFFICIENT_EVIDENCE, even if an explicitly labelled scenario value is calculated.
SOL/SUI parameter versions separate. Max price/tick/size filters fail closed.

## Shadow risk semantics and upgrade

P3 default policy: hard drawdown5% latches, denies new buys and cancels pending
entry remainders, retains existing protection and lets explicit protective exits
manage positions. No automatic liquidation due solely to the account hard lock.
P2 legacy force-liquidation behavior is retained only as explicitly named
`LEGACY_FORCED_LIQUIDATION_EXPERIMENT` compatibility and cannot be deployed as P3.
Prefer extension/composition with P2 ledger over rewriting. Preserve existing
2000 account/history/locks. No silent account reset or clearing locks on migration.
LIVE state and P2 risk immutable constants remain unchanged.
