from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from solbot.app_api.application import create_app
from solbot.app_api.auth import AuthStore
from solbot.app_api.data import DataReader
from solbot.app_api.notifications import NotificationStore
from solbot.app_api.ops_monitor import OpsMonitor
from solbot.ops.agent import (
    OpsBackend,
    is_orphan_sqlite_temp,
    mask_log_line,
    parse_fail2ban_output,
    parse_meminfo,
)
from tests.unit.test_p6_independent_qa import DeviceAuthenticationQA, FakeControl


class _FakeAgentClient:
    def __init__(self, backend: OpsBackend | None = None):
        self.backend = backend
        self.calls: list[dict] = []

    def call(self, payload: dict):
        self.calls.append(payload)
        if self.backend is None:
            return None
        return self.backend.handle(payload)


class OpsSystemStatusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.now_sec = 1_800_000_000.0
        self.now_ms = int(self.now_sec * 1000)

    def _build_backend(self) -> OpsBackend:
        meminfo = self.root / "meminfo"
        meminfo.write_text(
            "MemTotal:        2009088 kB\n"
            "MemFree:          120832 kB\n"
            "MemAvailable:    1027072 kB\n"
            "Buffers:          102400 kB\n"
            "Cached:           819200 kB\n"
            "SReclaimable:     174080 kB\n"
            "SwapTotal:       4193280 kB\n"
            "SwapFree:        3876864 kB\n",
            encoding="utf-8",
        )
        proc_stat = self.root / "stat"
        proc_stat.write_text("cpu  1000 0 500 8500 0 0 0 0 0 0\n", encoding="utf-8")
        uptime = self.root / "uptime"
        uptime.write_text("431340.50 800000.00\n", encoding="utf-8")
        loadavg = self.root / "loadavg"
        loadavg.write_text("0.89 0.85 0.95 2/210 12345\n", encoding="utf-8")
        drop_caches = self.root / "drop_caches"
        drop_caches.write_text("0\n", encoding="utf-8")

        r2_dir = self.root / "r2"
        r2_dir.mkdir(parents=True, exist_ok=True)
        (r2_dir / "r2-v2-shadow.sqlite").write_bytes(b"live-db")
        (r2_dir / "r2-v2-shadow.sqlite-wal").write_bytes(b"live-wal")
        (r2_dir / "r2-v2-shadow.sqlite-shm").write_bytes(b"live-shm")
        orphan = r2_dir / "r2-shadow-read.sqlite.doho8fo5"
        orphan.write_bytes(b"X" * 4096)
        old_time = self.now_sec - 3600
        os.utime(orphan, (old_time, old_time))

        auth_log = self.root / "auth.log"
        auth_log.write_text(
            "2026-11-15T14:00:01 sshd[10]: Failed password for invalid user admin from 1.2.3.4 port 51234 ssh2\n"
            "2026-11-15T14:01:01 sshd[11]: Accepted publickey for ubuntu from 10.7.0.2 port 40000 ssh2\n"
            "2026-11-15T14:02:01 sudo: token=supersecret123 password=shouldhide\n",
            encoding="utf-8",
        )
        health_log = self.root / "health.log"
        health_log.write_text("2026-11-15T14:00:00+08:00 status=ok load=0.89 mem_avail_mb=1003\n", encoding="utf-8")

        tmp_dir = self.root / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        stale_tmp = tmp_dir / "old_artifact.bin"
        stale_tmp.write_bytes(b"Y" * 2048)
        os.utime(stale_tmp, (self.now_sec - 5 * 86400, self.now_sec - 5 * 86400))

        return OpsBackend(
            drop_caches_path=drop_caches,
            meminfo_path=meminfo,
            proc_stat_path=proc_stat,
            uptime_path=uptime,
            loadavg_path=loadavg,
            sqlite_dirs=(r2_dir,),
            apt_cache_dir=self.root / "apt_missing",
            tmp_dir=tmp_dir,
            log_files={"auth": auth_log, "health": health_log, "fail2ban": self.root / "f2b.log"},
            clock=lambda: self.now_sec,
        )

    def test_meminfo_and_orphan_sqlite_guard(self):
        parsed = parse_meminfo(
            "MemTotal: 2048000 kB\nMemAvailable: 1024000 kB\nBuffers: 102400 kB\nCached: 409600 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n"
        )
        self.assertEqual(parsed["ram_total_mb"], 2000)
        self.assertEqual(parsed["ram_used_mb"], 1000)
        self.assertEqual(parsed["ram_pct"], 50.0)
        self.assertEqual(parsed["buff_cache_mb"], 500)

        r2 = self.root / "guard"
        r2.mkdir()
        for keep in ("a.sqlite", "a.sqlite-wal", "a.sqlite-shm", "a.sqlite-journal", "a.sqlite.wal"):
            p = r2 / keep
            p.write_bytes(b"1")
            os.utime(p, (self.now_sec - 7200, self.now_sec - 7200))
            self.assertFalse(is_orphan_sqlite_temp(p, now_sec=self.now_sec))

        orphan = r2 / "r2-shadow-read.sqlite.doho8fo5"
        orphan.write_bytes(b"1")
        os.utime(orphan, (self.now_sec - 1200, self.now_sec - 1200))
        self.assertTrue(is_orphan_sqlite_temp(orphan, now_sec=self.now_sec))

    def test_fail2ban_parser_and_log_masking(self):
        f2b = parse_fail2ban_output(
            "|- Filter\n|  |- Currently failed: 2\n|  |- Total failed: 9\n`- Actions\n   |- Currently banned: 1\n   `- Banned IP list: 8.8.8.8\n"
        )
        self.assertEqual(f2b["currently_failed"], 2)
        self.assertEqual(f2b["total_failed"], 9)
        self.assertEqual(f2b["currently_banned"], 1)
        self.assertEqual(f2b["banned_ips"], ["8.8.8.8"])

        masked = mask_log_line("auth password=MySecretValue token=abc123")
        self.assertNotIn("MySecretValue", masked)
        self.assertNotIn("abc123", masked)
        self.assertIn("password=***", masked)

    def test_optimize_memory_cooldown_and_clean_cache_safety(self):
        backend = self._build_backend()
        res1 = backend.optimize_memory()
        self.assertTrue(res1["ok"])
        self.assertEqual(backend.drop_caches_path.read_text(encoding="utf-8").strip(), "1")

        # Cooldown rejects immediate repeat
        res2 = backend.optimize_memory()
        self.assertFalse(res2["ok"])
        self.assertEqual(res2["code"], "OPTIMIZE_COOLDOWN")

        # Advance clock past cooldown
        self.now_sec += 65.0
        res3 = backend.optimize_memory()
        self.assertTrue(res3["ok"])

        # Dry-run clean_cache must not delete orphan or tmp
        dry = backend.clean_cache(dry_run=True)
        self.assertTrue(dry["dry_run"])
        self.assertGreater(dry["total_freed_bytes"], 0)
        self.assertTrue((self.root / "r2" / "r2-shadow-read.sqlite.doho8fo5").exists())
        self.assertTrue((self.root / "r2" / "r2-v2-shadow.sqlite").exists())
        self.assertTrue((self.root / "r2" / "r2-v2-shadow.sqlite-wal").exists())

        # Logs mask secrets & classify alerts
        logs = backend.logs(source="auth", only_alerts=False, limit=50)
        self.assertTrue(logs["ok"])
        self.assertEqual(logs["alert_count"], 1)
        self.assertTrue(all("supersecret123" not in e["text"] for e in logs["entries"]))

    def test_app_system_routes_require_auth_and_expose_modules(self):
        from cryptography.hazmat.primitives.asymmetric import ec
        qa = DeviceAuthenticationQA()
        qa.now = self.now_ms
        qa.key = ec.generate_private_key(ec.SECP256R1())
        auth = AuthStore(self.root / "auth.sqlite", clock=lambda: self.now_ms)
        qa.store = auth
        qa.session = qa.pair(qa.key)
        notif = NotificationStore(self.root / "notifications.sqlite", clock=lambda: self.now_ms)
        notif.publish_security_alert("HTTPS_API", "INVALID_SIGNATURE", source_ip="9.9.9.9")

        status_dir = self.root / "status"
        status_dir.mkdir()
        (status_dir / "micro_sol.json").write_text(
            json.dumps({"symbol": "SOLUSDT", "ready": True, "bid": "70.10", "received_time_ms": self.now_ms - 50}),
            encoding="utf-8",
        )
        (status_dir / "shadow.json").write_text(
            json.dumps({"status": "ready", "updated_at": self.now_ms - 500}),
            encoding="utf-8",
        )
        (status_dir / "r2.json").write_text(
            json.dumps({"status": "ready", "updated_at": self.now_ms - 500, "candidate": {"state": "WATCHING"}}),
            encoding="utf-8",
        )
        (status_dir / "guardian.json").write_text(
            json.dumps({"live_state": "LIVE_OFF", "locked": 0, "at": self.now_ms - 500}),
            encoding="utf-8",
        )
        (status_dir / "execution.json").write_text(
            json.dumps({"live_state": "LIVE_OFF", "real_orders": 0, "at": self.now_ms - 500}),
            encoding="utf-8",
        )

        backend = self._build_backend()
        ops = OpsMonitor(
            auth_db=auth.path,
            notifications_db=notif.path,
            status_paths={
                "micro_sol": status_dir / "micro_sol.json",
                "shadow": status_dir / "shadow.json",
                "r2": status_dir / "r2.json",
                "guardian": status_dir / "guardian.json",
                "execution": status_dir / "execution.json",
            },
            agent_client=_FakeAgentClient(backend),
            clock=lambda: self.now_ms,
        )
        app = create_app(auth, DataReader(), notif, FakeControl(), ops=ops)
        client = TestClient(app, base_url="https://testserver")

        # Unauthenticated rejected
        unauth = client.get("/api/v1/app/system/overview")
        self.assertEqual(unauth.status_code, 401)

        # Signed overview
        h_get = qa.sign("GET", "/api/v1/app/system/overview", b"", session=qa.session)
        ov = client.get("/api/v1/app/system/overview", headers=h_get)
        self.assertEqual(ov.status_code, 200)
        body = ov.json()
        self.assertTrue(body["server"]["ops_agent_connected"])
        # 1 pre-seeded alert + 1 alert generated by the rejected unauth request above
        self.assertEqual(body["server"]["security"]["api_alerts_24h"], 2)
        # AUTHENTICATED / PERMANENT in auth_audit must not count as auth failures
        self.assertEqual(body["server"]["security"]["api_auth_failures_24h"], 0)
        self.assertEqual(len(body["bot_modules"]["layers"]), 4)
        self.assertTrue(any(s.get("group") == "sui_research" for s in body["bot_modules"]["services"]))

        # API audit logs
        log_path = "/api/v1/app/system/logs?source=api&only_alerts=true&limit=20"
        h_log = qa.sign("GET", log_path, b"", session=qa.session)
        lr = client.get(log_path, headers=h_log)
        self.assertEqual(lr.status_code, 200)
        self.assertEqual(lr.json()["source"], "api")
        self.assertGreaterEqual(lr.json()["count"], 1)

        # Optimize memory
        h_opt = qa.sign("POST", "/api/v1/app/system/optimize-memory", b"{}", session=qa.session)
        opt_r = client.post("/api/v1/app/system/optimize-memory", content=b"{}", headers=h_opt)
        self.assertEqual(opt_r.status_code, 200)
        self.assertTrue(opt_r.json()["ok"])

        # Clean cache dry_run
        clean_body = b'{"dry_run":true}'
        h_clean = qa.sign("POST", "/api/v1/app/system/clean-cache", clean_body, session=qa.session)
        cl_r = client.post("/api/v1/app/system/clean-cache", content=clean_body, headers=h_clean)
        self.assertEqual(cl_r.status_code, 200)
        self.assertTrue(cl_r.json()["dry_run"])

        # Graceful fallback when ops agent is down
        ops_down = OpsMonitor(
            auth_db=auth.path,
            notifications_db=notif.path,
            status_paths={"micro_sol": status_dir / "micro_sol.json"},
            agent_client=_FakeAgentClient(None),
            clock=lambda: self.now_ms,
        )
        fb = ops_down.overview()
        self.assertFalse(fb["server"]["ops_agent_connected"])
        self.assertEqual(len(fb["bot_modules"]["layers"]), 4)


if __name__ == "__main__":
    unittest.main()
