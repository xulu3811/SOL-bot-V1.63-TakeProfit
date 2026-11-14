"""Comprehensive unit tests for Phase 1 pipeline fixes and Phase 2 strategy/APTS/P5-SOL upgrades."""
from dataclasses import replace
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from solbot.accounts.allocation_ledger import AllocationLedger
from solbot.accounts.balance_reconciliation import BalanceReconciliation
from solbot.app_api.binance_account import AccountVault, UrlTransport
from solbot.app_api.notifications import NotificationStore, SourceEventCollector
from solbot.exchange.account_reader import AccountReader
from solbot.exchange.binance_private import AuthorizationDenied, BinancePrivate, Credentials, MockTransport
from solbot.exchange.p5_reconciliation import PrivateHandshake
from solbot.exchange.user_stream import UserStream
from solbot.execution.p5_authorization import LiveSession, MockAuthorization
from solbot.execution.p5_engine import ExecutionEngine
from solbot.execution.p5_store import OrderStore
from solbot.execution.p5_system import build_execution_system
from solbot.mathematics.apts_exit import APTSState, update_protection
from solbot.mathematics.contracts import ACCOUNTS, make_context
from solbot.mathematics.entry_pricing import price_entry
from solbot.mathematics.trend_exhaustion import TrendEvidence
from solbot.p3_runtime import plan_signal
from solbot.risk.drawdown_engine import DrawdownEngine
from solbot.risk.p5_bridge import GuardianBridge
from solbot.risk.permission_engine import TradeIntent
from solbot.strategy import evaluate
from solbot.strategy.r1_reversal import assess as assess_r1
from solbot.strategy.scheduler import StrategyStore
from solbot.strategy.t1_pullback import assess as assess_t1


def _bars(closes, step_ms, end_ms, *, vols=None, buys=None, lows=None, highs=None):
    rows = []
    start = end_ms - len(closes) * step_ms
    for idx, close in enumerate(closes):
        open_t = start + idx * step_ms
        close_t = open_t + step_ms - 1
        op = closes[idx - 1] if idx > 0 else close
        lo = lows[idx] if lows is not None else min(op, close) - 0.1
        hi = highs[idx] if highs is not None else max(op, close) + 0.1
        vol = vols[idx] if vols is not None else 100.0
        buy = buys[idx] if buys is not None else vol * 0.6
        rows.append({
            "open_time": open_t, "close_time": close_t,
            "open": str(op), "high": str(hi), "low": str(lo), "close": str(close),
            "volume": str(vol), "quote_volume": str(vol * close),
            "taker_buy_base_volume": str(buy),
            "taker_buy_quote_volume": str(buy * close),
        })
    return rows


def _t1_fixture(*, close_1m=100.4, h4_close=123.8, h4_ema20=120.0, h1_adx=30.0, rsi_tail=(38.0, 44.0, 51.0), rvol_last=1.6):
    as_of = 1_794_400_500_000
    c5 = _bars([100.0 + i * 0.01 for i in range(60)], 300_000, as_of)
    c5[-2]["high"] = "100.1"
    c5[-1]["close"] = "100.3"
    c5[-1]["low"] = "100.2"
    c5[-1]["high"] = "100.35"
    c1 = _bars([100.0 for _ in range(30)], 60_000, as_of + 180_000)
    c1[-3].update({"low": "99.8", "high": "100.1", "open": "99.9", "close": "100.0"})
    c1[-2].update({"low": "99.9", "high": "100.2", "open": "100.0", "close": "100.15"})
    c1[-1].update({"low": "100.0", "open": "100.15", "close": str(close_1m), "high": str(max(close_1m + 0.05, 100.4))})
    c4 = _bars([h4_close] * 5, 14_400_000, as_of)
    candles = {"5m": c5, "1m": c1, "4h": c4}
    n5 = len(c5)
    metrics = {
        "5m": {
            "rsi6": [55.0] * (n5 - 3) + list(rsi_tail),
            "rvol20": [1.0] * (n5 - 1) + [rvol_last],
            "vwap20": [100.0] * n5,
        },
        "1h": {"adx14": [h1_adx]},
        "4h": {"ema20": [h4_ema20], "ema50": [110.0]},
    }
    return candles, metrics


class Phase1StoreAndSchedulerTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_accepted_after_honors_explicit_900s_expires_at(self):
        store = StrategyStore(self.root / "strategy.sqlite")
        self.addCleanup(store.close)
        store.configure("runtime", "SOLUSDT", self.root / "market.sqlite")
        cand_t = 1_800_000_000_000
        store.persist(
            "SOLUSDT",
            cand_t,
            [
                {
                    "strategy_id": "T1",
                    "candidate_time": cand_t,
                    "expires_at": cand_t + 900_000,
                    "accepted": True,
                    "reasons": [],
                    "signal_id": "sig-900",
                },
                {
                    "strategy_id": "R1",
                    "candidate_time": cand_t + 1,
                    "accepted": True,
                    "reasons": [],
                    "signal_id": "sig-legacy",
                },
            ],
            {"db": "test"},
            now_ms=None,
            cooldown_ms=0,
        )
        # At cand_t + 600s, the legacy fallback (300s) is expired while 900s signal remains active.
        rows = store.accepted_after(0, symbol="SOLUSDT", now_ms=cand_t + 600_000)
        self.assertEqual([r["signal_id"] for r in rows], ["sig-900"])
        # Past 900s, both are excluded.
        self.assertEqual(store.accepted_after(0, symbol="SOLUSDT", now_ms=cand_t + 900_000), [])

    def test_cooldown_blocks_within_60m_while_preserving_duplicate_candidate_priority(self):
        store = StrategyStore(self.root / "strategy.sqlite")
        self.addCleanup(store.close)
        store.configure("runtime", "SOLUSDT", self.root / "market.sqlite")
        t0 = 1_800_000_000_000
        first = store.persist(
            "SOLUSDT", t0,
            [{"strategy_id": "T1", "candidate_time": t0, "expires_at": t0 + 900_000,
              "accepted": True, "reasons": [], "signal_id": "sig-1"}],
            {}, now_ms=t0 + 1_000, cooldown_ms=3_600_000,
        )
        self.assertTrue(first[0]["accepted"])
        # Same candidate_time gets DUPLICATE_CANDIDATE, not cooldown.
        dup = store.persist(
            "SOLUSDT", t0 + 60_000,
            [{"strategy_id": "T1", "candidate_time": t0, "expires_at": t0 + 900_000,
              "accepted": True, "reasons": [], "signal_id": "sig-1-dup"}],
            {}, now_ms=t0 + 61_000, cooldown_ms=3_600_000,
        )
        self.assertIn("DUPLICATE_CANDIDATE", dup[0]["reasons"])
        self.assertNotIn("STRATEGY_COOLDOWN_ACTIVE", dup[0]["reasons"])
        # Distinct candidate within 60m gets cooldown blocked.
        t_within = t0 + 1_800_000
        blocked = store.persist(
            "SOLUSDT", t_within,
            [{"strategy_id": "T1", "candidate_time": t_within, "expires_at": t_within + 900_000,
              "accepted": True, "reasons": [], "signal_id": "sig-2"}],
            {}, now_ms=t_within + 1_000, cooldown_ms=3_600_000,
        )
        self.assertFalse(blocked[0]["accepted"])
        self.assertIn("STRATEGY_COOLDOWN_ACTIVE", blocked[0]["reasons"])
        # After 60m cooldown elapses, T1 accepts again.
        t_after = t0 + 3_600_001
        resumed = store.persist(
            "SOLUSDT", t_after,
            [{"strategy_id": "T1", "candidate_time": t_after, "expires_at": t_after + 900_000,
              "accepted": True, "reasons": [], "signal_id": "sig-3"}],
            {}, now_ms=t_after + 1_000, cooldown_ms=3_600_000,
        )
        self.assertTrue(resumed[0]["accepted"])


class Phase1RuntimeAndStreamTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def test_plan_signal_aligns_bnb_00075_fee_and_010_stop_distance(self):
        now = 1_800_000_000_000
        payload = {
            "symbol": "SOLUSDT", "strategy_id": "T1", "strategy_version": "p2-research-1",
            "signal_id": "t1-010-sig", "signal_time": now - 10_000, "candidate_time": now - 10_000,
            "expires_at": now + 600_000, "accepted": True, "score": "92.5",
            "stop_distance": "0.010", "reference_price": "150.00", "entry_high": "150.50",
            "indicators": {"5m": {"atr14": "0.50"}},
        }
        features = {
            "symbol": "SOLUSDT", "ready": True, "synced": True, "reasons": [],
            "bid": "149.90", "ask": "150.00", "mid": "149.95", "microprice": "149.96",
            "bid_qty": "25", "ask_qty": "25", "bids": [["149.90", "25"]], "asks": [["150.00", "25"]],
            "spread": "0.10", "spread_bps": "6.67", "imbalance": "0.15",
            "trade_ready": True, "trade_count": 20, "trade_volume": "100",
            "signed_volume": "8.0", "buy_ratio": "0.62", "short_volatility": "0.002",
            "trade_event_time_ms": now - 500, "trade_received_time_ms": now - 400,
            "quote_updates_per_sec": "2", "book_ticker_received_time_ms": now - 400,
            "received_time_ms": now - 400, "exchange_time_ms": now - 500, "feature_generated_time_ms": now - 400,
            "update_id": 42,
            "rules": {
                "symbol": "SOLUSDT", "status": "TRADING", "isSpotTradingAllowed": True,
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.01", "minPrice": "0.01", "maxPrice": "100000"},
                    {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001", "maxQty": "100000"},
                    {"filterType": "MIN_NOTIONAL", "minNotional": "5"},
                ],
            },
        }
        account = {
            "cash": "2000", "reserved": "0", "last_equity": "2000", "peak_equity": "2000",
            "positions": [], "pending_orders": [], "hard_locked": False,
        }
        _, _, planned = plan_signal(payload, features, account, now)
        self.assertEqual(planned["sizing"]["status"], "RESEARCH_SIZED")
        entry_fee_rate = Decimal(planned["cost"]["entry_fee"]) / Decimal(planned["cost"]["entry_notional"])
        exit_fee_rate = Decimal(planned["cost"]["exit_fee"]) / Decimal(planned["cost"]["exit_notional"])
        self.assertEqual(entry_fee_rate, Decimal("0.00075"))
        self.assertEqual(exit_fee_rate, Decimal("0.00075"))
        self.assertEqual(planned["pricing"]["status"], "INSUFFICIENT_EVIDENCE")
        self.assertEqual(planned["gate_reasons"], ["MATH_NOT_APPROVED"])
        ctx = make_context(
            symbol="SOLUSDT", account_id=ACCOUNTS["SOLUSDT"], strategy_id="T1",
            strategy_version="p2-research-1", signal_id="t1-010-price", decision_time_ms=now,
            features=features, inputs={"test": True}, parameter_version="p3-runtime-1",
        )
        direct_price = price_entry(ctx, features, signal_max_price="150.50", notional="500.00", atr="0.50", stop_distance="0.010")
        self.assertEqual(direct_price.status, "INSUFFICIENT_EVIDENCE")
        self.assertNotIn("STOP_OUTSIDE_ALLOWED_BAND", direct_price.reasons)
        with self.assertRaises(ValueError):
            price_entry(ctx, features, signal_max_price="150.50", notional="500.00", atr="0.50", stop_distance="0.020")

    def test_user_stream_cbot_routing_and_account_vault_event_application(self):
        now = 1_800_000_000_000
        gate = LiveSession(environment="mock", clock=lambda: now)
        gate.request_on(MockAuthorization(), lambda s: {k: True for k in ("account", "permissions", "orders", "balances", "protection", "guardian", "filters")})
        transport = MockTransport([])
        client = BinancePrivate(lambda: Credentials("k", "s"), transport, gate, clock=lambda: now)
        stream = UserStream(client)
        stream.acknowledge({"status": 200, "id": "sub-1", "result": {"subscriptionId": 7}})

        bot_fill = stream.receive({
            "subscriptionId": 7,
            "event": {
                "e": "executionReport", "E": now, "T": now, "s": "SOLUSDT",
                "c": "cbotby-1", "i": 55, "S": "BUY", "X": "FILLED", "x": "TRADE",
                "q": "2.000", "z": "2.000", "Z": "304.00", "l": "2.000", "L": "0",
            },
        })
        self.assertEqual(bot_fill["kind"], "ORDER")
        manual = stream.receive({
            "subscriptionId": 7,
            "event": {
                "e": "executionReport", "E": now, "T": now, "s": "SOLUSDT",
                "c": "manual-web-1", "i": 56, "S": "BUY", "X": "FILLED", "x": "TRADE",
                "q": "1.000", "z": "1.000", "Z": "150.00", "l": "1.000", "L": "150.00",
            },
        })
        self.assertEqual(manual["kind"], "MANUAL_ORDER")

        vault = AccountVault(self.root / "vault", clock=lambda: now, transport=UrlTransport())
        with vault._db() as db:
            db.execute(
                "INSERT INTO bot_orders(signal_id,client_order_id,exchange_order_id,price,quantity,quote,status,created_at,stop_client_id,stop_price) "
                "VALUES('sig1','cbotby-1','55','0','2.000','304','NEW',?,?,?)",
                (now - 5000, "cbot2s-stop1", "150.48"),
            )
            db.execute(
                "INSERT INTO protections VALUES('manual:SOL','cbotsl-m1','77','1.000','149.00','148.90','150.00','OPEN',?,?)",
                (now - 5000, now - 5000),
            )

        res1 = vault.apply_user_stream_event(bot_fill)
        self.assertTrue(res1["handled"])
        self.assertEqual(res1["status"], "FILLED")
        # Canceled stop (origClientOrderId in raw['C']) clears stop_client_id immediately.
        stop_cancel = stream.receive({
            "subscriptionId": 7,
            "event": {
                "e": "executionReport", "E": now + 100, "T": now + 100, "s": "SOLUSDT",
                "c": "cancel-req-1", "C": "cbot2s-stop1", "i": 58, "S": "SELL", "X": "CANCELED", "x": "CANCELED",
                "q": "2.000", "z": "0", "Z": "0", "l": "0", "L": "0",
            },
        })
        self.assertEqual(stop_cancel["kind"], "ORDER")
        res2 = vault.apply_user_stream_event(stop_cancel)
        self.assertEqual(res2["target"], "bot_stop")
        self.assertEqual(res2["status"], "CANCELED")

        with vault._db() as db:
            row = db.execute("SELECT status,price,stop_client_id,stop_price FROM bot_orders WHERE signal_id='sig1'").fetchone()
        self.assertEqual(row[0], "FILLED")
        self.assertEqual(Decimal(row[1]), Decimal("152"))
        self.assertEqual(row[2], "")

        prot_fill = vault.apply_user_stream_event({"s": "SOLUSDT", "c": "cbotsl-m1", "X": "FILLED", "T": now + 200})
        self.assertEqual((prot_fill["target"], prot_fill["status"]), ("protections", "CLOSED"))
        self.assertFalse(vault.apply_user_stream_event(manual)["handled"])


class Phase2APTSv2Tests(unittest.TestCase):
    def _ctx(self, now):
        return make_context(
            symbol="SOLUSDT", account_id="SOL-shadow", strategy_id="T1",
            strategy_version="p2-research-1", signal_id="apts-v2", decision_time_ms=now,
            features={"symbol": "SOLUSDT", "bid": "100.00", "ready": True, "exchange_time_ms": now},
            inputs={"test": True}, parameter_version="SOLUSDT-apts-research-1", ttl_ms=5000,
        )

    def test_stag12h_triggers_exit_with_marketable_limit_when_flat_after_12h(self):
        t0 = 1_800_000_000_000
        state = APTSState.create(
            self._ctx(t0), "100.00", "5.0", "0.01",
            stop_distance="0.010", entry_time_ms=t0, reversal_confirm_ticks=2,
        )
        normal = TrendEvidence(ready=True, atr=Decimal("0.4"), buy_ratio=Decimal("0.55"), previous_buy_ratio=Decimal("0.55"))
        # 11h elapsed, flat +0.2%: stays resting.
        t_11h = t0 + 39_600_000
        d11 = update_protection(state, replace(self._ctx(t_11h), decision_time_ms=t_11h, book_time_ms=t_11h, valid_until_ms=t_11h + 5000), "100.20", normal, tick_size="0.01")
        self.assertFalse(d11.exit_required)
        # 12h elapsed, +0.6% profit (>= 0.5%): NOT stagnant.
        t_12h = t0 + 43_200_000
        d12_up = update_protection(d11.state, replace(self._ctx(t_12h), decision_time_ms=t_12h, book_time_ms=t_12h, valid_until_ms=t_12h + 5000), "100.60", normal, tick_size="0.01")
        self.assertFalse(d12_up.exit_required)
        # 12h elapsed, +0.2% flat (< 0.5%): 12h stagnation exit is cancelled, position remains held until stop-loss.
        d12_flat = update_protection(d11.state, replace(self._ctx(t_12h), decision_time_ms=t_12h, book_time_ms=t_12h, valid_until_ms=t_12h + 5000), "100.20", normal, tick_size="0.01")
        self.assertFalse(d12_flat.exit_required)
        self.assertEqual(d12_flat.state.trigger_reason, "")
        self.assertNotIn("STAGNATION_12H_EXIT", d12_flat.reasons)

    def test_s2_reversal_hysteresis_requires_two_consecutive_ticks(self):
        t0 = 1_800_000_000_000
        state = APTSState.create(self._ctx(t0), "100.00", "5.0", "0.01", reversal_confirm_ticks=2)
        up = TrendEvidence(ready=True, atr=Decimal("0.4"), buy_ratio=Decimal("0.65"), previous_buy_ratio=Decimal("0.55"))
        rev = TrendEvidence(
            ready=True, atr=Decimal("0.4"), previous_atr=Decimal("0.4"),
            structure_broken=True, buy_ratio=Decimal("0.3"), previous_buy_ratio=Decimal("0.7"),
            rsi=Decimal("40"), previous_rsi=Decimal("52"),
        )
        # Advance into S2 (> +1.5%).
        t1 = t0 + 60_000
        d1 = update_protection(state, replace(self._ctx(t1), decision_time_ms=t1, book_time_ms=t1, valid_until_ms=t1 + 5000), "102.00", up, tick_size="0.01")
        self.assertEqual(d1.state.stage, "S2_ADAPTIVE_TRAILING")
        # First CONFIRMED_REVERSAL tick -> pending confirmation, no exit yet.
        t2 = t1 + 60_000
        d2 = update_protection(d1.state, replace(self._ctx(t2), decision_time_ms=t2, book_time_ms=t2, valid_until_ms=t2 + 5000), "101.80", rev, tick_size="0.01")
        self.assertFalse(d2.exit_required)
        self.assertIn("REVERSAL_PENDING_CONFIRMATION", d2.reasons)
        self.assertEqual(d2.state.reversal_hits, 1)
        # Non-reversal tick resets counter back to 0.
        t3 = t2 + 60_000
        d3 = update_protection(d2.state, replace(self._ctx(t3), decision_time_ms=t3, book_time_ms=t3, valid_until_ms=t3 + 5000), "101.90", up, tick_size="0.01")
        self.assertFalse(d3.exit_required)
        self.assertEqual(d3.state.reversal_hits, 0)
        # Two consecutive CONFIRMED_REVERSAL ticks -> exit!
        t4 = t3 + 60_000
        d4 = update_protection(d3.state, replace(self._ctx(t4), decision_time_ms=t4, book_time_ms=t4, valid_until_ms=t4 + 5000), "101.70", rev, tick_size="0.01")
        self.assertFalse(d4.exit_required)
        t5 = t4 + 60_000
        d5 = update_protection(d4.state, replace(self._ctx(t5), decision_time_ms=t5, book_time_ms=t5, valid_until_ms=t5 + 5000), "101.60", rev, tick_size="0.01")
        self.assertTrue(d5.exit_required)
        self.assertEqual(d5.state.trigger_reason, "CONFIRMED_REVERSAL")


class Phase2StrategyV2Tests(unittest.TestCase):
    def test_t1_v2_chase_and_htf_filters(self):
        candles, metrics = _t1_fixture()
        factors, reasons = assess_t1(candles, metrics, "UPTREND")
        self.assertEqual(reasons, [])
        self.assertTrue(all(v == 1.0 for v in factors.values()))

        # 1m chase > 0.6% -> ONE_MINUTE_CHASE_EXCEEDED
        chased_c, chased_m = _t1_fixture(close_1m=101.1)
        _, chase_reasons = assess_t1(chased_c, chased_m, "UPTREND")
        self.assertIn("ONE_MINUTE_CHASE_EXCEEDED", chase_reasons)

        # Weak HTF ADX < 23 or 4h < ema20 -> HIGHER_TIMEFRAME_WEAK_TREND
        weak_c, weak_m = _t1_fixture(h1_adx=19.0)
        _, htf_reasons = assess_t1(weak_c, weak_m, "UPTREND")
        self.assertIn("HIGHER_TIMEFRAME_WEAK_TREND", htf_reasons)

    def test_r1_guarded_macro_downtrend_veto_and_sealed_live_flag(self):
        as_of = 1_794_400_500_000
        c5 = _bars([100.0 - i * 0.05 for i in range(60)], 300_000, as_of, buys=[20.0] * 60, vols=[100.0] * 60)
        c5[-3]["close"] = "98.0"
        c5[-2]["low"] = "97.5"
        c5[-2]["close"] = "97.8"
        c5[-2]["taker_buy_base_volume"] = "30.0"
        c5[-1]["low"] = "97.6"
        c5[-1]["close"] = "98.2"
        c5[-1]["taker_buy_base_volume"] = "70.0"
        c5[-1]["taker_buy_quote_volume"] = str(70.0 * 98.2)
        c1 = _bars([98.1, 98.15, 98.25, 98.3], 60_000, as_of + 180_000)
        # 4h close 95.0 << 0.99 * ema50(105.0)
        c4 = _bars([95.0] * 5, 14_400_000, as_of)
        n5 = len(c5)
        metrics = {
            "5m": {"rsi6": [50.0] * (n5 - 3) + [18.0, 22.0, 31.0], "rvol20": [1.0] * (n5 - 1) + [1.8]},
            "4h": {"ema50": [105.0]},
        }
        _, reasons = assess_r1({"5m": c5, "1m": c1, "4h": c4}, metrics, "RANGE")
        self.assertIn("MACRO_DOWNTREND_GUARD", reasons)


class Phase2DeSuiAndP5SOLUnificationTests(unittest.TestCase):
    def test_sol_live_full_p5_guardian_and_protection_flow_with_symbol_isolation(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        now = 1_800_000_000_000

        ledger = AllocationLedger(root / "ledger.sqlite", account_id="SOL-live", symbol="SOLUSDT")
        guardian = GuardianBridge(ledger, clock=lambda: now)
        BalanceReconciliation(ledger).reconcile("init", {"USDT": {"free": "2000", "locked": "0"}}, now, account_id="SOL-live", at=now)
        DrawdownEngine(ledger).mark("init-mark", {}, now, at=now)

        gate = LiveSession(environment="mock", clock=lambda: now, account_id="SOL-live")
        gate.request_on(MockAuthorization(), lambda s: {k: True for k in ("account", "permissions", "orders", "balances", "protection", "guardian", "filters")})

        # Confirm cross-symbol isolation: SOL-live rejects SUIUSDT on private API while accepting SOLUSDT.
        transport = MockTransport([
            (200, {}, {"openOrders": []}),
        ])
        priv = BinancePrivate(lambda: Credentials("k", "s"), transport, gate, clock=lambda: now, account_id="SOL-live")
        priv.sync_time(now, sent_at=now - 10, received_at=now)
        with self.assertRaises(AuthorizationDenied):
            priv.request("GET", "/api/v3/openOrders", {"symbol": "SUIUSDT"})
        self.assertIn("openOrders", priv.request("GET", "/api/v3/openOrders", {"symbol": "SOLUSDT"}))

        info = {
            "symbol": "SOLUSDT", "status": "TRADING", "isSpotTradingAllowed": True,
            "orderTypes": ["STOP_LOSS_LIMIT"],
            "filters": [
                {"filterType": "PRICE_FILTER", "tickSize": "0.01", "minPrice": "0.01", "maxPrice": "100000"},
                {"filterType": "LOT_SIZE", "stepSize": "0.01", "minQty": "0.01", "maxQty": "10000"},
            ],
        }

        class _FakeSOL:
            def __init__(self):
                self.calls = []
            def place(self, **params):
                self.calls.append(params)
                if params["side"] == "BUY":
                    return {
                        "symbol": "SOLUSDT", "side": "BUY",
                        "clientOrderId": params["newClientOrderId"], "orderId": 101,
                        "status": "FILLED", "executedQty": "4.00", "cummulativeQuoteQty": "600.00",
                        "fills": [{"tradeId": 1, "qty": "4.00", "price": "150.00", "commission": "0.60", "commissionAsset": "USDT"}],
                    }
                return {
                    "symbol": "SOLUSDT", "side": "SELL",
                    "clientOrderId": params["newClientOrderId"], "orderId": 102,
                    "status": "NEW", "executedQty": "0", "cummulativeQuoteQty": "0",
                }
            def account(self):
                return {"canTrade": True, "permissions": ["SPOT"], "balances": [{"asset": "USDT", "free": "1399.40", "locked": "0"}, {"asset": "SOL", "free": "4.00", "locked": "0"}]}
            def open_orders(self, symbol):
                return []

        ex = _FakeSOL()
        engine = ExecutionEngine(
            OrderStore(root / "orders.sqlite", account_id="SOL-live"), ex, guardian, gate,
            filters=lambda *a: None, protection_ready=lambda s: True, clock=lambda: now,
        )
        system = build_execution_system(
            root / "system", engine, ex, guardian, gate, lambda s: info, lambda s: {"bid": "150.00", "at": now},
            clock=lambda: now, account_id="SOL-live", symbol="SOLUSDT",
        )
        intent = TradeIntent("pos-sol-1", "sig-sol-1", "SOL-live", "SOLUSDT", "T1", "p2-research-1", "150.00", "4.00", "148.50", now, now + 60_000, now, now)
        order = engine.submit_entry(intent)
        self.assertEqual(order["state"], "FILLED")
        self.assertEqual(system.status("pos-sol-1")["state"], "PROTECTED")
        pos = guardian.position("pos-sol-1")
        self.assertEqual(pos["asset"], "SOL")
        self.assertEqual(Decimal(pos["robot_owned_quantity"]), Decimal("4.00"))
        self.assertEqual(ex.calls[1]["symbol"], "SOLUSDT")
        self.assertEqual(ex.calls[1]["type"], "STOP_LOSS_LIMIT")

        # Notifications honor SOL-live -> SOLUSDT attribution.
        notif_store = NotificationStore(root / "notif.sqlite", clock=lambda: now)

        class _FakeReader:
            def read(self, source, sql, params):
                if source == "guardian" and "FROM risk_events" in sql:
                    return [{"source_sequence": 1, "kind": "HARD_LOCK", "event_id": "hl1", "payload": json.dumps({"account_id": "SOL-live"}), "created_at": now}]
                return []
            def live_status(self):
                return {"available": True, "state": "HARD_LOCKED", "account_id": "SOL-live"}

        SourceEventCollector(_FakeReader(), notif_store).poll()
        with notif_store.tx() as db:
            symbols = [r[0] for r in db.execute("SELECT symbol FROM notification_events ORDER BY rowid")]
        self.assertEqual(symbols, ["SOLUSDT", "SOLUSDT"])


if __name__ == "__main__":
    unittest.main()
