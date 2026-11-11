# P2 execution contract

This package contains no exchange secret reader, private HTTP adapter, account stream, or executable real-order transport. `LiveExecutor` always denies in P2. A typed interface is not acceptance of real trading safety.

`LiveControl(path, symbol="SUIUSDT", account_id="SUI-live")` owns a separate SQLite file. `snapshot()` is authoritative; `request_on(AuthorizationRequest(actor, request_id, authenticated))` returns `DENY_NOT_READY` even with an authenticated caller. The boolean authentication context is an internal interface for a future authenticated server boundary, not a credential or HTTP authorization mechanism. There is no LIVE HTTP endpoint. `request_off()` cannot clear `HARD_LOCKED`; `force_lock()` persists it. A previously ON/PENDING state becomes PAUSED on restart and cannot pass the unimplemented safety checks. R1 has an explicit live denial. The local CLI is `python -m solbot.execution.live --database PATH status|on|off|lock`; mutation requires the OS administrator.

`ShadowExecutor(path, symbol="SOLUSDT", account_id="SOL-shadow", fee_rate="0.001")` owns a different SQLite file. The fixed initial cash is 2,000 USDT. Existing-file ownership is checked read-only before initialization; pointing either execution store at market, strategy, another account/symbol, or the other channel fails before journal/schema modification.

Public shadow methods:

- `submit_candidate(candidate, *, notional="300", now_ms=None, limit_price=None)` returns a pending order or raises `ShadowRejected` with `.code`. The default limit is `entry_high`. Candidate prices are decimal strings; its symbol, accepted status, strategy, time, entry range, and risk flags are validated. Only the descriptive research flags and R1's live-disabled flag are allowed in shadow. Hard/unknown flags are denied.
- `process_quote(symbol, bid, ask, timestamp_ms, *, now_ms=None)` accepts a fresh, ordered, positive public bid/ask and returns `{processed, code, fills}`. Quotes must be no more than 30 seconds old, not future-dated, newer than the previous quote, and later than the order's creation to fill it. Expired orders do not fill. No candle high/low inference or fabricated intervening quote is used.
- `submit_sell(position_id, limit_price, *, now_ms, request_id, expires_at=None)` reserves that entire position against a second sell. It remains pending until a later eligible quote. `cancel(order_id)` releases reservations.
- `snapshot()`, `orders(limit=100)`, and `positions(limit=100)` expose isolated ledger state. History query limits are capped at 1,000; snapshot open positions/pending orders are bounded by the two-slot limit.

All reservations/fills use SQLite `BEGIN IMMEDIATE` transactions. Pending BUY orders reserve notional plus estimated fees and occupy a slot together with open positions. Notional stays within 300–700 USDT, with at most two slots. Signal retries are idempotent; conflicting reuse is rejected. A completed order is returned on retry without creating another fill. Account/symbol/request identity is durable.

The 0.001 fee rate is a research estimate, not a claim about actual Binance fees. The simulator uses full fills at the later observed ask/bid, no depth, no partial fills, and no venue quantity/filter or queue-position model. It does not model BNB fee payment or BNB replenishment. Virtual SELL fees and realized PnL are recorded. SOL shadow results must not be presented as evidence of SUI live execution quality.

The five-percent drawdown cap is fixed in `RiskContract` and cannot be configured away. Shadow compares net liquidation equity with the persistent high-water mark on each fresh quote and after every individual fill. A breach cancels pending orders, virtually liquidates at the observed bid, and persists a hard lock with no reset API. Checking after each fill prevents another pending BUY from slipping through the same quote batch. Gaps/spreads can produce a loss beyond five percent before observation; this is a trigger, not a guaranteed execution-price floor.

Initial −0.8% stop, +1% profit protection, +1.5% APTS entry, and BNB 3/10/20-USDT parameters are immutable risk contracts for subsequent Guardian/APTS work. P2 does not pretend to execute their full protective-order mathematics. The shadow prototype supports pending manual/strategy limit exits and the account drawdown liquidation described above. `APTSParameters.implementation_status` is explicitly `NOT_IMPLEMENTED`.

Schema: `execution_owner` pins channel/account/symbol. Shadow uses `shadow_account`, `shadow_orders`, and `shadow_positions`; monetary values are TEXT decimal strings and times are UTC milliseconds. Live uses `live_control` and bounded `live_control_events`. These files are independent of P1 market storage and strategy snapshots.

`android/LiveControl.kt` is a minimal Material 3 Compose source component with an abstract authenticated repository contract. It derives ON exclusively from server snapshots, keeps unknown state explicit after communication errors, and disables hard-lock changes. No Android project/build scaffold or transport implementation was supplied, so compilation/device behavior is not claimed verified by Python tests.

Offline validation: `python -m unittest discover -s tests/unit -p test_p2_execution.py -v`. Tests exercise actual temporary ledgers, concurrent reservations/retries, sells and fee accounting, durable locks, per-fill drawdown gating, prewrite database isolation, and mock spies proving no secret-loader/private-client/order calls or socket connections on the checked paths.
