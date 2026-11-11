# P3-B pricing, execution estimates and sizing

All results are mathematical research proposals. `live_authorized` is immutable
false and cannot be supplied as a constructor argument. They do not replace the
P2 Guardian, which continues to reject live requests. No module in this work
package creates an account client, reads secrets, performs network I/O, or sends
orders. R1 has no live permission. No new dependency was added.

## Common contracts

`DecisionContext` contains `symbol, account_id, strategy_id, strategy_version,
signal_id, decision_time_ms, book_time_ms, valid_until_ms, input_hash`. Symbols
are bound to the existing accounts: SOLUSDT/SOL-shadow and SUIUSDT/SUI-live.
`make_context` creates the initial context with a 1..60000ms TTL (default5000),
optionally shortened to the strategy signal expiry. Expired contexts cannot be
created. `bind_context` hashes the parent context, complete computation inputs
and parameter version again, so each output binds the actual inputs used.
There is no wall-clock dependency inside the mathematical functions.

Every typed decision inherits frozen `DecisionBase(context, status,
parameter_version, reasons=())`, has `live_authorized=False`, and provides
`asdict()` for Decimal-to-string JSON serialization. Numeric money inputs must be
Decimal, decimal string or integer. Floats, booleans and nonfinite values fail
validation. `json_safe` rejects floats anywhere in audited inputs; convert P2
floating score/indicator strings explicitly before building a P3 context.

Public feature dictionaries follow `docs/p3-contract.md`. Entry, exit, sizing
and probability computations require a synchronized, ready, positive uncrossed
book whose event/receive timestamps are not future and are within2000ms. The
probability model also requires trades within its declared observation window.
An absent bookTicker exchange timestamp remains absent; no time is fabricated.

## Fees and expected return

`estimate_cost(context, *, entry_price, exit_price, quantity,
entry_liquidity='TAKER', exit_liquidity='TAKER', entry_slippage_bps='5',
exit_slippage_bps='10', uncertainty_bps='5', fees=FeeSchedule(), evidence=None)`
returns a `CostEstimate`. It adds entry/exit commissions on their respective
notionals, entry/exit slippage, and an uncertainty allowance on entry notional.
Defaults are research assumptions: maker/taker0.001; hypothetical BNB discount
0.25 is disabled unless explicitly selected. No account-specific fee rate or
BNB balance is inferred, and no BNB purchase is performed. Existing BNB3/10/20
requirements remain Guardian/future execution responsibilities.

`estimate_return(context, *, notional, profit_fraction, loss_fraction,
win_probability=None, cost=None, win_evidence=None, execution=None)` implements
`E = p*G - (1-p)*L - C/notional`, conditional on filled exposure. Multiplying by
notional gives expected net quote profit; optionally multiplying by the assumed
fill fraction produces a separately labelled scenario. Missing win probability
or costs yields no numerical net expectation. Supplied but unvalidated values
may produce scenario numbers, always marked INSUFFICIENT_EVIDENCE.

`Evidence(symbol, account_id, sample_count, out_of_sample, source_hash,
independently_reviewed=False)` is an external evidence declaration. The minimum
research gate requires100 samples, out-of-sample=True, independently_reviewed=True,
a source SHA256, and matching account/symbol. These declarations are not produced
by the bot and do not independently prove statistical reliability. No qualifying
empirical dataset was supplied in P3-B. Tests explicitly construct synthetic
evidence to exercise numerical positive paths; these fixtures are not real
validation. Future empirical evidence needs its own review. Costs/expectations
also bind signal, strategy, version, validity and cost input hash; future, stale
or mismatched estimates cannot support an entry candidate.

## Limit proposals and exchange rules

`price_entry(context, features, *, signal_max_price, notional, atr,
expected=None, cost=None, mode='AUTO', max_chase_bps='10', stop_distance='0.008',
reference_price=None, reference_minutes=None)` returns a `PricingDecision`.
Positive, qualified net expectation and cost evidence are mandatory for a
candidate. Missing evidence means modeREJECT/statusINSUFFICIENT_EVIDENCE.

The maximum buy price is rounded down from the minimum of the signal ceiling,
ask*(1+chase allowance), and ask*(1+half the positive expected net return).
The chase allowance is bounded to50bps and defaults to10bps. Short ATR above
0.8% of ask rejects an entry instead of widening the fixed initial stop.

PASSIVE_LIMIT stays at/below bid; IMPROVED_LIMIT must be strictly inside the
spread on a valid tick; MARKETABLE_LIMIT uses the ask rounded upward only when
it stays within the maximum. There is no unlimited chase. AUTO uses recomputed
microprice, buy ratio, spread ticks and net edge as explicit initial hypotheses.
Quantity is floored to stepSize without exceeding requested notional. If this
rounds below300USDT, the candidate is rejected rather than rounding above its
budget. Rejected output quantity is zero; out-of-protection computed prices are
omitted. No candidate represents a guaranteed fill or optimal empirically proven
price.

`price_exit(context, features, *, quantity, min_sell_price,
mode='MARKETABLE_LIMIT', max_slippage_bps='20', fees=FeeSchedule(),
reference_price=None, reference_minutes=None)` returns an `ExitDecision`.
The protected minimum is rounded up. A bid below it rejects the marketable limit
and explicitly preserves non-fill risk. It does not claim that a stop trigger
can execute at the protection price. Remaining quantity is floored; dust is
rejected. Passive/interior sell candidates remain possible when their requested
mode and protection permit them. APTS decides the protective intent separately.

`SymbolRules.from_exchange_info(raw, symbol=None)` parses the public rules;
`validate_order(price, quantity, side='BUY', reference_price=None,
reference_minutes=None)` returns rejection reasons. PRICE_FILTER, LOT_SIZE,
MIN_NOTIONAL/NOTIONAL, PERCENT_PRICE and PERCENT_PRICE_BY_SIDE are checked.
Percentage checks require the corresponding public reference and exact window;
a midprice is not invented as an exchange average. Missing dynamic reference
fails closed. Account-wide open-order/asset constraints still require Guardian;
these research proposals are not exchange authorization. Definitions were
checked against the [official Binance filters documentation](https://developers.binance.com/en/docs/products/spot/filters).

## Fill model and calibration

`estimate_fill(context, features, *, limit_price, quantity, wait_ms, side='BUY',
window_ms=60000, cancellation_probability='0.02')` returns an `ExecutionEstimate`.
For passive orders, a Poisson-style scenario hazard divides opposing observed
flow projected over the wait by twice the displayed same-side top depth plus
requested quantity, then discounts by distance/volatility. The resulting
`1-exp(-hazard)` is capped at0.70. Marketable limits use a displayed opposite-side
depth assumption capped at0.90. Queue priority, cancellations and hidden liquidity
are unobserved; touching/crossing does not mean100% fill.

Six mutually exclusive terminal outcomes sum to one: NO_FILL, PARTIAL_FILL,
FULL_FILL, TIMEOUT, CANCELLED, ADVERSE_FILL. Adverse fills are classified separately
from other filled outcomes. Partial outcomes assume half quantity and adverse
outcomes conservatively assume full quantity. These assumptions are exposed in
the output. Every estimate remains INSUFFICIENT_EVIDENCE and uncalibrated.

`FillObservation(predicted_any_fill, outcome, filled_fraction, source_hash)`
validates observed outcome consistency. `calibration_report(observations,
out_of_sample=False, minimum_samples=100)` returns Brier score, empirical any-fill
frequency and source hash, refuses duplicate evidence and bounds observations
to100000. Even adequate sample counts yield DIAGNOSTIC_ONLY; parameters are never
automatically updated and calibration alone does not certify future reliability.

## Position sizing

`size_position(context, features, *, score, entry_price, equity,
high_watermark, cash_balance, reserved_entry_notional='0',
reserved_entry_fees='0', open_positions=0, pending_entries=0,
hard_locked=False, entry_fee_rate='0.001', exit_fee_rate='0.001',
slippage_fraction='0.002', stop_distance='0.008', reference_price=None,
reference_minutes=None)` returns a `PositionSizingDecision`.

Cash balance means cash before pending-entry reservations; both notional and
fee reservations are subtracted exactly once. Score sizing is
`300+400*clip((score-75)/25,0,1)`. Risk sizing is
`equity*strategy_budget/(0.008+entry_fee+exit_fee+slippage)`; available sizing is
`max(0,cash-reservations)/(1+entry_fee)`. The minimum of these and700 is quantity
rounded down and checked against exchange filters and the300 floor. The research
risk budgets are T1=0.004 and R1=0.0025 with separate
`solusdt-risk-research-1` and `suiusdt-risk-research-1` versions, not transferred
profitability estimates.

Open positions plus pending entries must be below two. A latched hard lock or
drawdown >=5% rejects new sizing regardless of score; a subsequent equity recovery
does not override the supplied latched flag. This function never clears or
persists a lock; the existing account/Guardian owns its durable state. Sizing
does not liquidate or cancel existing protective exits. All remaining orders,
reservations, risk concurrency and idempotency belong to the executor.

## Verification scope

`python -m unittest discover -s tests/unit -p test_p3_pricing.py -v` covers22 tests:
independent Decimal fee/net-return/hazard/sizing formulas; three limit modes and
rejection; maximum price, increments and filters; future/stale/mismatched data;
probability conservation and calibration diagnostics; fee-inclusive reservations;
two slots and the exact5% boundary; separate symbol versions; immutable live
denial; reproducible context hashes. These are mathematical and contract tests,
not evidence of actual fill quality or stable strategy profitability.
