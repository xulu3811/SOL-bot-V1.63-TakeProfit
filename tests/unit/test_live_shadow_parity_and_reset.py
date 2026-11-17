import base64
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec

from solbot.app_api.auth import AuthStore, canonical
from solbot.app_api.application import create_app
from solbot.app_api.data import DataReader
from solbot.app_api.notifications import NotificationStore
from solbot.app_api.ops_monitor import OpsMonitor
from solbot.execution.live_policy import (
    DEFAULT_FEE_RATE,
    DEFAULT_SLIPPAGE_RATE,
    compute_entry_limit_price,
    compute_live_order_size,
    r2_risk_notional,
    strategy_entry_allowed,
)
from solbot.execution.p3_shadow import P3ShadowExecutor
from solbot.execution.r2_shadow import R2ShadowExecutor
from solbot.execution.shadow import ShadowRejected
from solbot.mathematics.contracts import SymbolRules
from solbot.mathematics.trend_exhaustion import TrendEvidence
from solbot.ops.agent import OpsBackend
from solbot.p3_runtime import plan_live_aligned_entry
from solbot.storage.r2_store import R2Store
from solbot.storage.read_snapshot import publish

NOW = 1_794_759_000_000
EXCHANGE_INFO = {
    "symbol": "SOLUSDT",
    "status": "TRADING",
    "isSpotTradingAllowed": True,
    "filters": [
        {"filterType": "PRICE_FILTER", "tickSize": "0.01", "minPrice": "0.01", "maxPrice": "10000"},
        {"filterType": "LOT_SIZE", "stepSize": "0.001", "minQty": "0.001", "maxQty": "10000"},
        {"filterType": "NOTIONAL", "minNotional": "5", "maxNotional": "1000000"},
    ],
}
RULES = SymbolRules.from_exchange_info(EXCHANGE_INFO, "SOLUSDT")


def _ready_features(bid="71.27", ask="71.29", now_ms=NOW):
    return {
        "symbol": "SOLUSDT",
        "ready": True,
        "synced": True,
        "reasons": [],
        "bid": bid,
        "ask": ask,
        "bid_qty": "120",
        "ask_qty": "120",
        "bid_quantity": "120",
        "ask_quantity": "120",
        "bids": [[bid, "120"]],
        "asks": [[ask, "120"]],
        "microprice": bid,
        "midpoint": bid,
        "spread": "0.02",
        "spread_bps": "2.8",
        "exchange_time_ms": now_ms - 20,
        "received_time_ms": now_ms - 10,
        "received_at_ms": now_ms - 10,
        "trade_event_time_ms": now_ms - 30,
        "trade_received_time_ms": now_ms - 10,
        "book_ticker_received_time_ms": now_ms - 10,
        "trade_volume": "100",
        "trade_count": 50,
        "quote_updates_per_sec": "2",
        "update_id": 42,
        "short_volatility": "0.002",
        "imbalance": "0.15",
        "buy_ratio": "0.55",
        "rules": EXCHANGE_INFO,
    }


class LiveShadowParityTests(unittest.TestCase):
    def test_narrow_r2_stop_below_50bps_rejected_in_live_and_shadow(self):
        # Reproduces 00:10 BJT R2 signal with 0.439% stop distance
        live_size = r2_risk_notional(
            "3151.79",
            "0.0043931847",
            DEFAULT_FEE_RATE,
            "85",
            min_order_usdt="1500",
            max_order_usdt="1500",
            is_custom_cfg=True,
        )
        self.assertIsNone(live_size)

        with tempfile.TemporaryDirectory() as folder:
            r2 = R2ShadowExecutor(Path(folder) / "r2.sqlite", rules=RULES, fee_rate="0.00075")
            r2.apply_trading_config("1500", "1500", 2)
            intent = {
                "symbol": "SOLUSDT",
                "strategy_id": "R2",
                "strategy_version": "r2-rebound-2",
                "signal_id": "r2-narrow-stop-0010",
                "accepted": True,
                "reasons": [],
                "signal_time": NOW,
                "expires_at": NOW + 60_000,
                "entry_low": "71.27",
                "entry_high": "71.48",
                "candidate_low": "71.00",
                "stop_price": "70.95",
                "stop_distance": "0.0043931847",
                "score": "85",
            }
            with self.assertRaises(ShadowRejected) as ctx:
                r2.submit_intent(intent, NOW, bid="71.27", ask="71.29")
            self.assertEqual(ctx.exception.code, "R2_RISK_BELOW_MINIMUM")

    def test_r2_chase_and_limit_price_align_with_live(self):
        with tempfile.TemporaryDirectory() as folder:
            r2 = R2ShadowExecutor(Path(folder) / "r2.sqlite", rules=RULES, fee_rate="0.00075")
            intent = {
                "symbol": "SOLUSDT",
                "strategy_id": "R2",
                "strategy_version": "r2-rebound-2",
                "signal_id": "r2-chase-test",
                "accepted": True,
                "reasons": [],
                "signal_time": NOW,
                "expires_at": NOW + 60_000,
                "entry_low": "71.20",
                "entry_high": "71.40",
                "candidate_low": "70.50",
                "stop_price": "70.50",
                "stop_distance": "0.0098",
                "score": "90",
            }
            with self.assertRaises(ShadowRejected) as ctx:
                r2.submit_intent(intent, NOW, bid="71.45", ask="71.46")
            self.assertEqual(ctx.exception.code, "CHASE_EXCEEDED")

            order = r2.submit_intent(intent, NOW, bid="71.10", ask="71.12")
            live_px, err = compute_entry_limit_price("R2", "71.20", "71.40", "71.10", "71.12", "0.01")
            self.assertIsNone(err)
            self.assertEqual(Decimal(order["limit_price"]), live_px)
            self.assertEqual(Decimal(order["limit_price"]), Decimal("71.16"))

            # Reproduce 10:08 R2 scenario: ask=71.85 with entry_high=72.06555 gets +5bps buffer -> 71.89 (caps at entry_high)
            px_1008, err_1008 = compute_entry_limit_price("R2", "71.85", "72.06555", "71.84", "71.85", "0.01")
            self.assertIsNone(err_1008)
            self.assertEqual(px_1008, Decimal("71.89"))
            px_cap, err_cap = compute_entry_limit_price("R2", "71.85", "72.06555", "72.04", "72.05", "0.01")
            self.assertIsNone(err_cap)
            self.assertEqual(px_cap, Decimal("72.06"))

    def test_p3_live_aligned_entry_filters_r1_downtrend_and_matches_live_pricing(self):
        with patch.dict("os.environ", {"SOLBOT_LIVE_STRATEGIES": "T1,T2,R2"}):
            ok_r1, reason_r1 = strategy_entry_allowed("R1", "UPTREND")
            self.assertFalse(ok_r1)
            self.assertEqual(reason_r1, "STRATEGY_NOT_ALLOWED")

            account = {"cash": "3300", "reserved": "0", "last_equity": "3300"}
            features = _ready_features(bid="71.30", ask="71.32", now_ms=NOW + 1000)
            base_payload = {
                "symbol": "SOLUSDT",
                "strategy_id": "T1",
                "strategy_version": "t1-v1",
                "signal_id": "t1-live-align-1",
                "accepted": True,
                "reasons": [],
                "regime": "UPTREND",
                "signal_time": NOW,
                "expires_at": NOW + 60_000,
                "entry_low": "71.40",
                "entry_high": "71.55",
                "score": "88",
            }
            # DOWNTREND blocks T1 just like Live AccountVault._parse_signal_payload
            _, deny_down = plan_live_aligned_entry(
                dict(base_payload, regime="DOWNTREND"),
                features,
                account,
                NOW + 1000,
                RULES,
                shared_cfg={"min_order_usdt": "1500", "max_order_usdt": "1500", "max_slots": 2},
            )
            self.assertEqual(deny_down, "T1_BLOCKED_IN_DOWNTREND")

            # UPTREND uses min(entry_low, bid)=71.30 and custom sizing 1500
            plan, deny_up = plan_live_aligned_entry(
                base_payload,
                features,
                account,
                NOW + 1000,
                RULES,
                shared_cfg={"min_order_usdt": "1500", "max_order_usdt": "1500", "max_slots": 2},
            )
            self.assertIsNone(deny_up)
            self.assertEqual(Decimal(plan["limit_price"]), Decimal("71.30"))
            self.assertEqual(Decimal(plan["notional"]), Decimal("1500"))
            self.assertEqual(plan["stop_distance"], "0.01")

    def test_p3_protection_uses_live_slippage_rate(self):
        with tempfile.TemporaryDirectory() as folder:
            p3 = P3ShadowExecutor(Path(folder) / "p3.sqlite", rules=RULES, fee_rate="0.00075")
            cand = {
                "symbol": "SOLUSDT",
                "strategy_id": "T1",
                "strategy_version": "t1-v1",
                "signal_id": "p3-slip-align-1",
                "accepted": True,
                "reasons": [],
                "signal_time": NOW,
                "expires_at": NOW + 60_000,
                "entry_low": "71.00",
                "entry_high": "71.30",
                "stop_distance": "0.010",
            }
            p3.submit_candidate(cand, notional="500", limit_price="71.00", now_ms=NOW + 100)
            p3.process_market(
                _ready_features(bid="70.95", ask="70.96"),
                TrendEvidence(ready=True),
                now_ms=NOW + 200,
            )
        # Note: verify compute_live_order_size checks min_qty & returns clean sizing
        qty, _ord_n, notional, err = compute_live_order_size(
            "T1", "71.00", "2000", "2000", "0.010", score="100", step_size="0.001", min_qty="0.001"
        )
        self.assertIsNone(err)
        self.assertEqual(notional, Decimal("1000"))
        self.assertGreater(qty, Decimal("14"))
        self.assertEqual(DEFAULT_SLIPPAGE_RATE, Decimal("0.00075"))


class ShadowResetTests(unittest.TestCase):
    def _build_env(self, root: Path):
        p3_db = root / "sol-shadow.sqlite"
        p3_read = root / "sol-shadow-read.sqlite"
        r2_db = root / "r2-v2-shadow.sqlite"
        r2_read = root / "r2-shadow-read.sqlite"
        r2_state = root / "r2-v2-state.sqlite"
        r2_state_read = root / "r2-state-read.sqlite"
        guard_db = root / "shadow-entry-guard.sqlite"
        p3_status = root / "sol-shadow-status.json"
        r2_status = root / "r2-status.json"
        cfg_path = root / "trading-config.json"

        cfg_path.write_text(json.dumps({"min_order_usdt": "1500", "max_order_usdt": "1500", "max_slots": 2}), encoding="utf-8")

        p3 = P3ShadowExecutor(p3_db, rules=RULES, fee_rate="0.00075", minimum_notional="500", maximum_notional="1000")
        p3.apply_trading_config("1500", "1500", 2)
        with p3._transaction() as db:
            db.execute(
                "INSERT INTO shadow_orders(order_id,request_key,fingerprint,account_id,symbol,strategy_id,signal_id,side,status,limit_price,quantity,notional,reserved,created_at,expires_at,candidate_json) VALUES (?,?,?,?,?,?,?,'BUY','FILLED','70','20','1400','0',?,?,?)",
                ("o1", "BUY:s1", "fp1", "SOL-shadow", "SOLUSDT", "T1", "s1", NOW, NOW + 60000, "{}"),
            )
            db.execute(
                "INSERT INTO shadow_positions(position_id,account_id,symbol,strategy_id,signal_id,status,quantity,entry_price,entry_cost,entry_fee,realized_pnl,opened_at) VALUES ('p-o1','SOL-shadow','SOLUSDT','T1','s1','CLOSED','20','70','1400','1.05','42.5',?)",
                (NOW,),
            )
            db.execute(
                "INSERT INTO shadow_fills(fill_id,order_id,position_id,event_time,side,quantity,price,fee,input_hash,assumptions) VALUES ('f1','o1','p-o1',?,'BUY','20','70','1.05','h1','{}')",
                (NOW,),
            )
            db.execute("UPDATE shadow_account SET cash='3342.5',last_equity='3342.5',peak_equity='3342.5',hard_locked=1,lock_reason='test'")

        r2 = R2ShadowExecutor(r2_db, rules=RULES, fee_rate="0.00075")
        r2.apply_trading_config("1500", "1500", 2)
        with r2._transaction() as db:
            db.execute(
                "INSERT INTO shadow_orders(order_id,request_key,fingerprint,account_id,symbol,strategy_id,signal_id,side,status,limit_price,quantity,notional,reserved,created_at,expires_at,candidate_json) VALUES (?,?,?,?,?,?,?,'BUY','FILLED','71','20','1420','0',?,?,?)",
                ("ro1", "BUY:rs1", "rfp1", "SOL-shadow", "SOLUSDT", "R2", "rs1", NOW, NOW + 60000, "{}"),
            )
            db.execute(
                "INSERT INTO shadow_fills(fill_id,order_id,position_id,event_time,side,quantity,price,fee,input_hash,assumptions) VALUES ('rf1','ro1','p-ro1',?,'BUY','20','71','1.06','h2','{}')",
                (NOW,),
            )
            db.execute("UPDATE shadow_account SET cash='3290',last_equity='3290',peak_equity='3300'")

        store = R2Store(r2_state)
        try:
            store.connection.execute(
                "INSERT INTO r2_outbox(signal_id,payload,status,outcome) VALUES ('r2-pend-1','{}','PENDING',NULL)",
            )
            store.connection.execute(
                "INSERT INTO r2_state(singleton,as_of_ms,payload) VALUES (1, ?, ?)",
                (NOW, json.dumps({"state": "TRIGGERED", "as_of_ms": NOW})),
            )
            store.connection.commit()
        finally:
            store.close()

        with closing(sqlite3.connect(guard_db)) as gdb:
            gdb.execute("CREATE TABLE shadow_entry_claims(signal_id TEXT PRIMARY KEY, strategy_id TEXT NOT NULL, signal_time INTEGER NOT NULL, claimed_at INTEGER NOT NULL, status TEXT NOT NULL)")
            gdb.execute("INSERT INTO shadow_entry_claims VALUES ('sig-1','R2',?,?,'CLAIMED')", (NOW, NOW))
            gdb.commit()

        publish(p3_db, p3_read)
        publish(r2_db, r2_read)
        publish(r2_state, r2_state_read)
        p3_status.write_text(json.dumps({"status": "ready", "updated_at": NOW, "shadow": {"cash": "3342.5"}}), encoding="utf-8")
        r2_status.write_text(json.dumps({"status": "ready", "updated_at": NOW, "shadow": {"cash": "3290"}}), encoding="utf-8")

        return {
            "p3_db": p3_db,
            "p3_read_db": p3_read,
            "r2_db": r2_db,
            "r2_read_db": r2_read,
            "r2_state_db": r2_state,
            "r2_state_read_db": r2_state_read,
            "entry_guard_db": guard_db,
            "p3_status": p3_status,
            "r2_status": r2_status,
            "trading_config": cfg_path,
        }

    def test_ops_backend_reset_clears_shadow_data_and_calibrates_equity_without_losing_services(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            paths = self._build_env(root)
            calls = []
            backend = OpsBackend(shadow_paths=paths, manage_services=True, clock=lambda: (NOW + 5000) / 1000.0)
            with patch.object(backend, "_service_action", side_effect=lambda act, units: calls.append((act, units))):
                result = backend.reset_shadow_data()

            self.assertTrue(result["ok"])
            self.assertEqual(result["initial_capital_usdt"], "3300")
            self.assertEqual(result["cleared_orders"], 2)
            self.assertEqual(result["cleared_fills"], 2)
            self.assertEqual(result["cleared_pending_outbox"], 1)
            self.assertEqual([c[0] for c in calls], ["stop", "start"])

            # Verify DataReader against published read snapshots yields 0 PnL, 0 orders, 3300 equity
            reader = DataReader(
                paths={
                    "shadow": paths["p3_read_db"],
                    "r2_shadow": paths["r2_read_db"],
                    "shadow_status": paths["p3_status"],
                    "r2_status": paths["r2_status"],
                },
                clock=lambda: NOW + 5000,
            )
            st = reader.shadow_status()
            self.assertEqual(Decimal(st["initial_capital"]), Decimal("3300"))
            self.assertEqual(Decimal(st["equity"]), Decimal("3300"))
            self.assertEqual(Decimal(st["pnl"]), Decimal("0"))
            self.assertEqual(st["total_trades"], 0)
            self.assertEqual(st["active_positions"], 0)
            self.assertEqual(reader.shadow_orders()["items"], [])

            # Re-attaching P3ShadowExecutor keeps metadata valid without double-adding virtual cash
            restarted_p3 = P3ShadowExecutor(paths["p3_db"], rules=RULES, fee_rate="0.00075", minimum_notional="500", maximum_notional="1000")
            restarted_p3.apply_trading_config("1500", "1500", 2)
            self.assertEqual(Decimal(restarted_p3.snapshot()["last_equity"]), Decimal("3300"))

    def test_notification_reset_shadow_cursors_re_enables_new_simulated_trades(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            paths = self._build_env(root)
            notif = NotificationStore(root / "notifications.sqlite", clock=lambda: NOW + 10_000)
            notif.publish("live-1", "BUY_FILL", "SOLUSDT", NOW, {"mode": "LIVE"})
            notif.publish("sim-old", "SIMULATED_TRADE", "SOLUSDT", NOW, {"mode": "SIMULATED"})
            with notif.tx() as db:
                db.execute("INSERT INTO notification_source_cursors VALUES ('shadow:shadow_fills', 99)")
                db.execute("INSERT INTO notification_source_cursors VALUES ('r2_shadow_read:shadow_fills', 88)")
            notif.reset_shadow_cursors()
            with notif.tx() as db:
                cursors = dict(db.execute("SELECT source, cursor FROM notification_source_cursors").fetchall())
                events = [r[0] for r in db.execute("SELECT kind FROM notification_events").fetchall()]
            self.assertNotIn("shadow:shadow_fills", cursors)
            self.assertNotIn("r2_shadow_read:shadow_fills", cursors)
            self.assertEqual(events, ["BUY_FILL"])

    def test_shadow_reset_endpoint_requires_confirm_and_auth_then_succeeds(self):
        from fastapi.testclient import TestClient

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            auth = AuthStore(root / "auth.sqlite", clock=lambda: NOW)
            priv = ec.generate_private_key(ec.SECP256R1())
            pem = priv.public_key().public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            ).decode()

            def sign_headers(method, path, body_bytes, seq, device_id=None, token=None):
                nonce = f"nonce_value_{seq:012d}"
                rid = f"req_id_value_{seq:012d}"
                sig = base64.b64encode(
                    priv.sign(
                        canonical(method, path, body_bytes, NOW, nonce, rid),
                        ec.ECDSA(hashes.SHA256()),
                    )
                ).decode()
                h = {
                    "X-Timestamp": str(NOW),
                    "X-Nonce": nonce,
                    "X-Request-Id": rid,
                    "X-Signature": sig,
                }
                if device_id and token:
                    h["X-Device-Id"] = device_id
                    h["Authorization"] = f"Bearer {token}"
                return h

            pair_body = json.dumps({"public_key_pem": pem, "device_passphrase": "pass-reset-test-1234", "device_name": "SM-S926U1"}).encode()
            session = auth.pair(pair_body, sign_headers("POST", "/api/v1/app/devices/pair", pair_body, 1))
            device_id = session["device_id"]
            token = session["session_token"]

            fake_ops = unittest.mock.MagicMock(spec=OpsMonitor)
            fake_ops.reset_shadow_data.return_value = {"ok": True, "cleared_orders": 3, "message": "reset ok"}
            fake_notif = unittest.mock.MagicMock(spec=NotificationStore)
            fake_control = unittest.mock.MagicMock()
            fake_reader = unittest.mock.MagicMock(spec=DataReader)
            fake_reader.live_status.return_value = {"state": "LIVE_OFF"}
            fake_reader.live_risk.return_value = {}
            fake_reader.shadow_status.return_value = {"synced": True}
            fake_reader.market_summary.return_value = {}
            fake_reader.strategy_summary.return_value = {}

            app = create_app(
                auth=auth,
                reader=fake_reader,
                notifications=fake_notif,
                control=fake_control,
                ops=fake_ops,
            )
            client = TestClient(app, base_url="https://testserver")

            r_bad = client.post(
                "/api/v1/app/shadow/reset",
                content=b"{}",
                headers=sign_headers("POST", "/api/v1/app/shadow/reset", b"{}", 2, device_id, token),
            )
            self.assertEqual(r_bad.status_code, 400)
            self.assertEqual(r_bad.json()["error"], "RESET_CONFIRMATION_REQUIRED")

            body_ok = b'{"confirm":true}'
            r_ok = client.post(
                "/api/v1/app/shadow/reset",
                content=body_ok,
                headers=sign_headers("POST", "/api/v1/app/shadow/reset", body_ok, 3, device_id, token),
            )
            self.assertEqual(r_ok.status_code, 200)
            self.assertTrue(r_ok.json()["ok"])
            fake_ops.reset_shadow_data.assert_called_once()
            fake_notif.reset_shadow_cursors.assert_called_once()
