# P2 deployment and research scope

Requirement change: SUIUSDT is the live candidate; SOLUSDT is SOL-shadow. Both collect five native public kline intervals. Their performance is not an execution-quality comparison because they trade different assets. SUI parameters are research hypotheses, not validated SOL parameter transfers.

The running P1 SOL service remains pinned to its existing release and database. P2 source is deployed under `/opt/sol-bot/p2` using a separate release symlink. The new SUI public market service binds loopback8001 and writes `/var/lib/sol-bot/database/sui-market.sqlite`. Each market database has its own serialized write channel and persisted symbol ownership. Existing backups include `.sqlite` files.

Strategy computation runs separately from ingestion and reads the market databases. It consumes finalized candle timestamps, uses bounded rolling windows, persists snapshots and checkpoints, and rejects incomplete/unaligned/future data. Simulation is a separate ledger with2000USDT virtual funds. A0.1% per-side fee is a configurable research estimate, not an authenticated Binance account rate. Pending limits require a later eligible public quote; depth, partial fills, queue priority and execution probability remain P5 work.

The LIVE protocol is server-persisted. P2 always rejects live enable and execution because the complete Guardian, private-account adapter and protective order state machine are not present. No private transport or secret provider is loaded. Local administrator control is provided without exposing a public LIVE endpoint. The Compose control is a source component with an abstract authenticated repository contract; complete Android transport and application build belong to P6.

Risk constants preserve300–700USDT per entry, two simultaneous slots, initial stop0.8%, protection at1%, APTS threshold above1.5%, hard drawdown5%, and BNB fee-token refill trigger3USDT/target10USDT/active cap20USDT. P2 math interfaces expose these contracts without claiming that full Guardian/APTS or replenishment execution exists. R1 live remains denied independently of its research score.

Recovery: stop only new P2 strategy/shadow/SUI services if needed; leave `solbot-market.service` and its source symlink intact. Restore the previous `/opt/sol-bot/p2` release link before restarting P2. Preserve SQLite files and review migration compatibility before rollback. Back up state using SQLite online backup; never clear LIVE locks or simulation history as a restart workaround.

Source documentation for public data https://github.com/binance/binance-spot-api-docs/blob/master/rest-api.md and https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md.

Security tracking: previously exposed management credentials remain HIGH pending independent invalidation proof. No new public ingress or SSH/UFW change is authorized by this deployment. User confirmed console access and password rotation in the prior stage. Specific external Cursor/Grok audit facilities are unavailable in this session; the report distinguishes available independent code review from the requested external review.
