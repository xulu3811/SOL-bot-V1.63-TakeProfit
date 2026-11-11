"""Offline execution isolation, durability and atomic risk-reservation tests."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from solbot.execution import (AuthorizationRequest, ExecutionRouter, LiveControl, LiveExecutor,
                              LiveState, Mode, OrderRequest, ShadowExecutor, ShadowRejected)
from solbot.risk import APTSParameters, Guardian, RiskContract

NOW = 1_794_355_200_000


def candidate(index=0, **changes):
    result = {"symbol": "SOLUSDT", "strategy_id": "T1", "signal_id": f"signal-{index}",
              "signal_time": NOW, "expires_at": NOW + 300_000, "accepted": True,
              "reasons": [], "risk_flags": ["RESEARCH_PARAMETERS_UNVALIDATED", "EXECUTION_QUALITY_UNOBSERVED"],
              "entry_low": "99", "entry_high": "100", "strategy_version": "p2-v1",
              "regime": "UPTREND", "score": 85, "indicators": {}}
    result.update(changes)
    return result


class LiveSafetyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "live.sqlite"
        self.control = LiveControl(self.path)
        self.secret_loader, self.private_factory, self.private_order = Mock(), Mock(), Mock()
        self.private_factory.return_value.order = self.private_order
        self.live = LiveExecutor(self.control, secret_loader=self.secret_loader, private_client_factory=self.private_factory)
        self.request = OrderRequest(Mode.LIVE, "SUI-live", "SUIUSDT", "T1", "request-1", "BUY", "1", "300")

    def tearDown(self):
        self.directory.cleanup()

    def assert_no_private_access(self):
        self.secret_loader.assert_not_called()
        self.private_factory.assert_not_called()
        self.private_order.assert_not_called()

    def test_first_start_off_and_no_secret_private_or_order_calls(self):
        with patch("socket.socket.connect", side_effect=AssertionError("network forbidden")) as network:
            self.assertEqual(self.control.snapshot()["state"], "LIVE_OFF")
            for _ in range(5):
                self.assertFalse(self.live.execute(self.request).accepted)
            network.assert_not_called()
        self.assert_no_private_access()

    def test_request_on_is_denied_even_with_authenticated_user(self):
        result = self.control.request_on(AuthorizationRequest("user", "on-1", True))
        self.assertEqual(result.code, "DENY_NOT_READY")
        self.assertEqual(result.state, "LIVE_OFF")
        self.assertFalse(self.control.snapshot()["live_ready"])
        self.assertFalse(self.live.execute(self.request).accepted)
        self.assert_no_private_access()

    def test_unauthenticated_control_denies_and_does_not_transition(self):
        result = self.control.request_on(AuthorizationRequest("user", "on-2", False))
        self.assertEqual(result.code, "DENY_UNAUTHENTICATED")
        self.assertEqual(self.control.snapshot()["revision"], 1)

    def test_hard_lock_survives_off_on_and_restart(self):
        self.control.force_lock()
        self.assertEqual(self.control.request_off().state, "HARD_LOCKED")
        self.assertEqual(self.control.request_on(AuthorizationRequest("user", "on-3", True)).code, "DENY_HARD_LOCKED")
        restarted = LiveControl(self.path)
        self.assertEqual(restarted.snapshot()["state"], "HARD_LOCKED")
        self.assertFalse(LiveExecutor(restarted).execute(self.request).accepted)
        self.assert_no_private_access()

    def test_restart_does_not_restore_previously_on_without_safety_acceptance(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE live_control SET state='LIVE_ON'")
            db.commit()
        restarted = LiveControl(self.path)
        self.assertEqual(restarted.snapshot()["state"], "PAUSED")
        self.assertFalse(LiveExecutor(restarted).execute(self.request).accepted)

    def test_even_forced_on_cannot_bypass_guardian_or_enable_r1(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE live_control SET state='LIVE_ON'")
            db.commit()
        self.assertEqual(self.live.execute(self.request).code, "DENY_NOT_IMPLEMENTED")
        self.assertEqual(self.live.execute(replace(self.request, strategy_id="R1")).code, "DENY_R1_NOT_APPROVED")
        self.assert_no_private_access()

    def test_shadow_and_foreign_symbol_cannot_enter_live(self):
        self.assertEqual(self.live.execute(replace(self.request, mode=Mode.SHADOW)).code, "DENY_CHANNEL_ISOLATION")
        self.assertEqual(self.live.execute(replace(self.request, symbol="SOLUSDT")).code, "DENY_ACCOUNT_SYMBOL")
        self.assert_no_private_access()

    def test_live_file_ownership_is_durable(self):
        with self.assertRaises(ValueError):
            LiveControl(self.path, symbol="SOLUSDT")
        with self.assertRaises(ValueError):
            ShadowExecutor(self.path)

    def test_risk_contract_cannot_relax_and_apts_is_explicitly_unimplemented(self):
        for changes in ({"maximum_drawdown": Decimal("0.06")}, {"maximum_slots": 3},
                        {"maximum_notional": Decimal("701")}, {"initial_stop_loss": Decimal("0.01")}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                RiskContract(**changes)
        self.assertFalse(Guardian().authorize_live(self.request).allowed)
        self.assertEqual(APTSParameters().implementation_status, "NOT_IMPLEMENTED")
        with self.assertRaises(ValueError):
            APTSParameters(implementation_status="READY")


class ShadowLedgerTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "shadow.sqlite"
        self.shadow = ShadowExecutor(self.path)

    def tearDown(self):
        self.directory.cleanup()

    def quote(self, offset, bid="99.9", ask="100"):
        return self.shadow.process_quote("SOLUSDT", bid, ask, NOW + offset, now_ms=NOW + offset)

    def test_pending_reserves_cash_fees_and_never_fills_on_creation_quote(self):
        order = self.shadow.submit_candidate(candidate(), notional="700", now_ms=NOW)
        self.assertEqual(order["status"], "PENDING")
        self.assertEqual(Decimal(self.shadow.snapshot()["reserved"]), Decimal("700.700"))
        self.assertEqual(Decimal(self.shadow.snapshot()["available_cash"]), Decimal("1299.300"))
        self.assertEqual(self.quote(0)["fills"], [])
        self.assertEqual(self.quote(1000, "100", "100.1")["fills"], [])
        fills = self.quote(2000)["fills"]
        self.assertEqual(len(fills), 1)
        self.assertEqual(fills[0]["status"], "FILLED")
        self.assertEqual(Decimal(self.shadow.snapshot()["cash"]), Decimal("1299.3"))
        self.assertEqual(Decimal(self.shadow.snapshot()["reserved"]), 0)

    def test_later_better_price_releases_unused_reservation(self):
        self.shadow.submit_candidate(candidate(), notional="300", now_ms=NOW)
        self.quote(1000, "98.9", "99")
        self.assertEqual(Decimal(self.shadow.snapshot()["cash"]), Decimal("1702.703"))
        self.assertEqual(Decimal(self.shadow.snapshot()["reserved"]), 0)

    def test_virtual_sell_records_fees_pnl_and_reserves_position_once(self):
        self.shadow.submit_candidate(candidate(), notional="300", now_ms=NOW)
        self.quote(1000)
        position = self.shadow.snapshot()["positions"][0]
        order = self.shadow.submit_sell(position["position_id"], "101", now_ms=NOW + 2000, request_id="sell-1")
        self.assertEqual(order["status"], "PENDING")
        with self.assertRaises(ShadowRejected) as caught:
            self.shadow.submit_sell(position["position_id"], "101", now_ms=NOW + 2000, request_id="sell-2")
        self.assertEqual(caught.exception.code, "DENY_QUANTITY_RESERVED")
        self.assertEqual(self.quote(3000, "100.5", "100.6")["fills"], [])
        self.assertEqual(len(self.quote(4000, "101", "101.1")["fills"]), 1)
        self.assertEqual(Decimal(self.shadow.snapshot()["cash"]), Decimal("2002.397"))
        self.assertEqual(Decimal(self.shadow.positions()[0]["realized_pnl"]), Decimal("2.397"))
        self.assertEqual(self.shadow.snapshot()["positions"], [])

    def test_idempotent_retry_and_restart_preserve_orders_cash_and_positions(self):
        original = self.shadow.submit_candidate(candidate(), now_ms=NOW)
        restarted = ShadowExecutor(self.path)
        repeated = restarted.submit_candidate(candidate(), now_ms=NOW + 1000)
        self.assertEqual(original["order_id"], repeated["order_id"])
        self.assertEqual(len(restarted.orders()), 1)
        with self.assertRaises(ShadowRejected):
            restarted.submit_candidate(candidate(), notional="400", now_ms=NOW + 1000)
        self.quote(2000)
        self.assertEqual(len(ShadowExecutor(self.path).snapshot()["positions"]), 1)
        self.assertEqual(Decimal(ShadowExecutor(self.path).snapshot()["cash"]), Decimal("1699.7"))

    def test_concurrent_reservations_never_exceed_two_slots_or_cash(self):
        def submit(index):
            try:
                return ShadowExecutor(self.path).submit_candidate(candidate(index), notional="700", now_ms=NOW)
            except ShadowRejected as exc:
                return exc.code
        with ThreadPoolExecutor(max_workers=10) as pool:
            results = list(pool.map(submit, range(20)))
        self.assertEqual(sum(isinstance(result, dict) for result in results), 2)
        self.assertTrue(all(isinstance(result, dict) or result == "DENY_MAX_SLOTS" for result in results))
        snapshot = self.shadow.snapshot()
        self.assertEqual(len(snapshot["pending_orders"]), 2)
        self.assertEqual(Decimal(snapshot["reserved"]), Decimal("1401.4"))
        self.assertGreaterEqual(Decimal(snapshot["available_cash"]), 0)

    def test_concurrent_same_signal_has_exactly_one_reservation(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            orders = list(pool.map(lambda _: self.shadow.submit_candidate(candidate(), now_ms=NOW), range(20)))
        self.assertEqual(len({row["order_id"] for row in orders}), 1)
        self.assertEqual(len(self.shadow.orders()), 1)
        self.assertEqual(Decimal(self.shadow.snapshot()["reserved"]), Decimal("300.3"))

    def test_expiration_and_cancel_release_reserves_without_fake_fills(self):
        first = self.shadow.submit_candidate(candidate(0, expires_at=NOW + 2000), now_ms=NOW)
        second = self.shadow.submit_candidate(candidate(1), now_ms=NOW)
        self.shadow.cancel(second["order_id"])
        self.quote(2000)
        statuses = {row["order_id"]: row["status"] for row in self.shadow.orders()}
        self.assertEqual(statuses[first["order_id"]], "EXPIRED")
        self.assertEqual(statuses[second["order_id"]], "CANCELLED")
        self.assertEqual(Decimal(self.shadow.snapshot()["cash"]), 2000)
        self.assertEqual(Decimal(self.shadow.snapshot()["reserved"]), 0)

    def test_hard_five_percent_drawdown_liquidates_and_survives_restart(self):
        self.shadow.submit_candidate(candidate(), notional="700", now_ms=NOW)
        self.quote(1000)
        self.shadow.submit_candidate(candidate(1), notional="700", now_ms=NOW + 2000)
        result = self.quote(3000, "80", "80.1")
        self.assertEqual(result["code"], "HARD_LOCKED")
        snapshot = ShadowExecutor(self.path).snapshot()
        self.assertTrue(snapshot["hard_locked"])
        self.assertEqual(snapshot["pending_orders"], [])
        self.assertEqual(snapshot["positions"], [])
        self.assertEqual(Decimal(snapshot["reserved"]), 0)
        self.assertEqual(Decimal(snapshot["cash"]), Decimal("1858.74"))
        self.assertEqual(self.shadow.positions()[0]["close_reason"], "hard_drawdown_lock")
        with self.assertRaises(ShadowRejected) as caught:
            ShadowExecutor(self.path).submit_candidate(candidate(2), now_ms=NOW + 4000)
        self.assertEqual(caught.exception.code, "DENY_HARD_LOCKED")

    def test_first_fill_drawdown_stops_other_buys_in_same_quote_transaction(self):
        ledger = ShadowExecutor(Path(self.directory.name) / "wide-spread.sqlite", fee_rate="0")
        for index in range(2):
            ledger.submit_candidate(candidate(index), notional="700", now_ms=NOW)
        result = ledger.process_quote("SOLUSDT", "70", "100", NOW + 1000, now_ms=NOW + 1000)
        buys = [row for row in ledger.orders() if row["side"] == "BUY"]
        self.assertEqual(sum(row["status"] == "FILLED" for row in buys), 1)
        self.assertEqual(sum(row["status"] == "CANCELLED" for row in buys), 1)
        self.assertEqual(result["code"], "HARD_LOCKED")
        self.assertEqual({row["side"] for row in result["fills"]}, {"BUY", "SELL"})
        restarted = ShadowExecutor(Path(self.directory.name) / "wide-spread.sqlite", fee_rate="0")
        self.assertTrue(restarted.snapshot()["hard_locked"])
        self.assertEqual(Decimal(restarted.snapshot()["cash"]), Decimal("1790"))
        self.assertEqual(restarted.snapshot()["positions"], [])
        self.assertEqual(restarted.snapshot()["pending_orders"], [])

    def test_symbol_account_and_channel_files_are_isolated(self):
        with self.assertRaises(ValueError):
            ShadowExecutor(self.path, symbol="SUIUSDT")
        with self.assertRaises(ValueError):
            ShadowExecutor(self.path, account_id="other-shadow")
        with self.assertRaises(ValueError):
            LiveControl(self.path)
        with self.assertRaises(ShadowRejected):
            self.shadow.submit_candidate(candidate(symbol="SUIUSDT"), now_ms=NOW)
        with self.assertRaises(ShadowRejected):
            self.shadow.process_quote("SUIUSDT", "1", "1.1", NOW, now_ms=NOW)
        sui = ShadowExecutor(Path(self.directory.name) / "sui.sqlite", symbol="SUIUSDT", account_id="SUI-shadow")
        sui.submit_candidate(candidate(symbol="SUIUSDT"), now_ms=NOW)
        self.assertEqual(len(self.shadow.orders()), 0)
        self.assertEqual(len(sui.orders()), 1)

    def test_wrong_database_is_rejected_before_schema_or_journal_mutation(self):
        def tables_and_mode(path):
            with closing(sqlite3.connect(path)) as db:
                return list(db.execute("SELECT name,sql FROM sqlite_master ORDER BY name")), db.execute("PRAGMA journal_mode").fetchone()[0]
        for table in ("market_klines", "strategy_decisions"):
            path = Path(self.directory.name) / (table + ".sqlite")
            with closing(sqlite3.connect(path)) as db:
                db.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")
                db.commit()
            before = tables_and_mode(path)
            for constructor in (ShadowExecutor, LiveControl):
                with self.subTest(table=table, constructor=constructor.__name__), self.assertRaises(ValueError):
                    constructor(path)
                self.assertEqual(tables_and_mode(path), before)
        before = tables_and_mode(self.path)
        with self.assertRaises(ValueError):
            LiveControl(self.path)
        self.assertEqual(tables_and_mode(self.path), before)
        live_path = Path(self.directory.name) / "separate-live.sqlite"
        LiveControl(live_path)
        before = tables_and_mode(live_path)
        with self.assertRaises(ValueError):
            ShadowExecutor(live_path)
        self.assertEqual(tables_and_mode(live_path), before)

    def test_rejected_candidates_and_size_constraints_do_not_mutate_ledger(self):
        variants = [candidate(accepted=False), candidate(reasons=["DATA_NOT_READY"]),
                    candidate(risk_flags=["DATA_NOT_READY"]), candidate(signal_time=NOW + 1),
                    candidate(expires_at=NOW), candidate(entry_low="101"), candidate(strategy_id="UNKNOWN")]
        for item in variants:
            with self.subTest(item=item), self.assertRaises((ShadowRejected, ValueError)):
                self.shadow.submit_candidate(item, now_ms=NOW)
        for size in ("299.99", "700.01", "-1", "NaN"):
            with self.subTest(size=size), self.assertRaises((ShadowRejected, ValueError)):
                self.shadow.submit_candidate(candidate(), notional=size, now_ms=NOW)
        self.assertEqual(self.shadow.orders(), [])
        self.assertEqual(Decimal(self.shadow.snapshot()["cash"]), 2000)

    def test_stale_future_duplicate_and_wrong_unit_quotes_cannot_fill(self):
        self.shadow.submit_candidate(candidate(), now_ms=NOW)
        self.assertFalse(self.shadow.process_quote("SOLUSDT", "99", "100", NOW + 1000, now_ms=NOW + 32_000)["processed"])
        self.assertFalse(self.shadow.process_quote("SOLUSDT", "99", "100", NOW + 1000, now_ms=NOW)["processed"])
        with self.assertRaises(ValueError):
            self.shadow.process_quote("SOLUSDT", "99", "100", NOW // 1000, now_ms=NOW)
        self.quote(1000)
        self.assertFalse(self.quote(1000)["processed"])
        self.assertEqual(len(self.shadow.snapshot()["positions"]), 1)

    def test_shadow_router_cannot_invoke_live_even_on_shadow_failure(self):
        live_spy = Mock()
        router = ExecutionRouter(self.shadow, live=live_spy)
        request = OrderRequest(Mode.SHADOW, "SOL-shadow", "SOLUSDT", "T1", "route-1", "BUY", "100", "300")
        with patch("socket.socket.connect", side_effect=AssertionError("network forbidden")) as network:
            order = router.execute(request, candidate=candidate(), now_ms=NOW)
            self.assertEqual(order["status"], "PENDING")
            denied = router.execute(replace(request, symbol="SUIUSDT"), candidate=candidate(), now_ms=NOW)
            self.assertFalse(denied.accepted)
            network.assert_not_called()
        live_spy.execute.assert_not_called()

    def test_r1_descriptive_live_block_does_not_block_shadow_research(self):
        order = self.shadow.submit_candidate(candidate(strategy_id="R1", risk_flags=["R1_LIVE_PERMANENTLY_DISABLED"]), now_ms=NOW)
        self.assertEqual(order["strategy_id"], "R1")
        self.assertEqual(order["status"], "PENDING")


if __name__ == "__main__":
    unittest.main()
