# P2 research strategy and replay contract

This package uses finalized P1 public candles only, imports no execution code,
and performs no network/account/order calls. All thresholds are initial research
assumptions, unvalidated separately for SOLUSDT and SUIUSDT. R1 is research-only;
its descriptive `R1_LIVE_PERMANENTLY_DISABLED` flag is always present.

`evaluate(symbol, candles_by_interval, as_of_ms)` returns T1 and R1 evaluation
records, including rejection reasons. Input is P1 candle dictionaries. Price
outputs and indicator values are decimal strings; float arithmetic is used only
inside indicators and is rounded deterministically to ten decimal places at
serialization. Scores are numerical research priorities, not probabilities.

## Definitions and time safety

Each interval uses its most recent 120 completed bars, with at least 60 required.
Every included timestamp must be UTC aligned, unique, contiguous, symbol-correct,
and its last close must match the most recent expected close at `as_of_ms`.
The boundary is `close_time <= as_of_ms`; open/future bars are excluded. Gaps or
stale high timeframes produce DATA_NOT_READY. Missing history outside the bounded
calculation window is additionally guarded by the orchestrator's P1 health check.
Pure evaluation never consults the wall clock. There is no arrival-delay model;
historical replay assumes a bar was available when it finalized. Inputs corrected
later by REST are identifiable by snapshot source hashes; archive the source
SQLite copy used for an audit replay.

* EMA20/50 seed with the SMA of the first period; subsequent alpha is 2/(n+1).
* RSI6 starts from six close-to-close gains/losses and uses Wilder smoothing.
  Flat gain/loss is 50; no losses with positive gain is 100; no gains is zero.
* ATR14 uses true ranges beginning at bar 1 with bar 0 as previous close, seeds
  their first 14-value mean, then uses Wilder smoothing.
* ADX14 uses directional movement from adjacent high/low pairs, Wilder-smoothed
  TR/+DM/-DM, DX=100*abs(+DI--DI)/(+DI+-DI), then seeds ADX with its first 14 DX.
  The first ADX is at zero-based index 27. Zero directional movement means DX=0.
* RVOL20 divides current base volume by the preceding 20 bars' mean. The current
  volume is excluded from that baseline. A zero baseline is not ready.
* VWAP20 is the rolling sum of actual exchange quote volume divided by the sum
  of base volume over 20 bars including the current completed bar. This is not
  a daily anchored VWAP or a typical-price approximation.

An UPTREND requires 4h and 1h EMA20>EMA50, 1h ADX>=20, and 15m EMA20>=EMA50,
close>=EMA50, and a nondecreasing low across the latest/prior three-bar blocks.
DOWNTREND requires both 4h/1h down with 1h ADX>=25. RANGE requires 1h ADX<20
and absolute three-bar EMA20 change divided by the latest 1h close <=0.003.
Other combinations are UNCERTAIN.

T1 requires an UPTREND, one of the prior three 5m RSI6 values in [32,48], current
RSI rising above 48, RVOL>=1.2, and either VWAP reclamation or retained four-bar
support plus a close above the previous 5m high. R1 requires RANGE, one of the
prior four RSI values <=20, current RSI rising above 28, RVOL>=1.5, current taker
buy quote ratio >=0.5 and above the prior two ratios' mean, a nondecreasing 5m low
and close above the prior high. Accelerating consecutive declines with rising ADX
are rejected. Neither oversold RSI nor volume alone can trigger an acceptance.

Both require three-bar 1m confirmation: middle low > first low, final low >=
middle low, and a bullish final close above both prior highs. That final 1m bar
must close after the 5m candidate. Candidate expiration is the 5m close +5min,
exclusive; the first confirmation may reject and a later one may accept before
expiry. Entry range is current 1m close through close*1.001, only a candidate
range, not an exchange-tick-rounded executable order.

Versioned score weights are trend20, momentum25, participation20, structure20,
execution15. >=85 HIGH, >=75 STANDARD, >=60 WATCH, otherwise REJECT. Mandatory
strategy conditions and ATR/close in (0,0.03] remain independent hard gates.
Only the volatility half of execution quality is observable from these inputs,
so it earns at most 7.5/15; no spread/depth/liquidity credit is invented. Current
fully valid hypotheses score92.5. This initial binary grading has limited ranking
resolution and is not optimized. Defaults are immutable/versioned and have no
automatic parameter-update mechanism.

## Durable interface

`StrategyStore(path)` opens a separate WAL database; it refuses any file containing
`market_klines` before writing. `Scheduler(market_db, store, symbol)` uses read-only
market connections, indexed candle-open bounds, and bounded 120-row windows.
Higher timeframe indicator calculations are reused through a bounded 96-window
cache; the runtime refreshes its source windows each tick to see REST corrections.

`tick(now_ms=None)` returns processed/counts/reasons/regimes/samples. On first
startup it processes only the latest finalized 1m bar. Later ticks process up to
1440 missed closed bars; historical accepted hypotheses that have expired are
stored rejected with EXPIRED_DURING_CATCHUP. Each event atomically writes both
decisions, one provenance snapshot, and its symbol checkpoint. Restart or repeated
events cannot advance them separately. Accepted five-minute candidate keys are
unique per symbol, preventing duplicate or simultaneous conflicting intents.

`accepted_after(after_id=0, symbol=None, now_ms=None, limit=100)` returns flat
candidate mappings with added integer `decision_id`. Runtime consumers should
always supply current `now_ms` to exclude expired decisions, and persist their
own idempotent consumer cursor. Order/fill state remains the executor's concern.

Tables:

* `decisions`: id, symbol, strategy_id, signal_time, candidate_time, accepted,
  strategy_version, signal_id, payload_json. `signal_id` is a deterministic SHA256
  of strategy version, symbol, strategy ID and 1m signal time. `payload_json`
  contains the entire evaluation, factor explanations and all five indicator
  snapshots. No accepted intent can repeat the same (symbol,candidate_time).
* `checkpoints`: symbol, last_close_time, strategy_version.
* `snapshots`: symbol, as_of_ms, strategy_version, payload_json. Snapshot includes
  exact decisions, bar ranges/counts and SHA256 of the bounded relevant OHLCV
  values per interval. It is not a second full candle archive.
* `strategy_metadata`: fixed mode/runtime-or-replay, version and market source
  path per symbol. Different modes/versions/sources require separate stores.

Historical decisions and compact snapshots are retained for audit; runtime memory
is bounded. Monitor disk growth and establish an agreed archival policy before
long unattended runs. Deleting decision history would invalidate deduplication.

## CLI and actual P1 replay

```sh
PYTHONPATH=backend/src python -m solbot.strategy.scheduler --market-db market.sqlite --strategy-db strategy.sqlite --symbol SOLUSDT --once
PYTHONPATH=backend/src python -m solbot.strategy.scheduler --market-db archived-p1.sqlite --strategy-db replay-a.sqlite --symbol SOLUSDT --replay --start-ms 1793746800000 --end-ms 1794351599999 --report replay-a.json
```

Use real source timestamps and a distinct empty replay store for each repeat.
`Scheduler(...).replay(start_ms,end_ms)` is the equivalent API, paginates 1000 1m
events, and computes from strictly historical rolling inputs. A replay store is
prohibited from the runtime accepted feed. JSON output contains separate T1/R1
accepted/rejected counts, rejection frequencies, market-state counts and first
accepted/rejected examples. Replay validates deterministic signal generation;
it contains no profitability, slippage, fill-probability or out-of-sample claim.
