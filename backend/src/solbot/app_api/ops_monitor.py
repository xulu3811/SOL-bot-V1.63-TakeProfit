"""Read-only system & SOL-bot modular status monitor + AF_UNIX ops client."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import socket
import sqlite3
import struct
import threading
import time
from typing import Any


_BJT = timezone(timedelta(hours=8))

DEFAULT_STATUS_PATHS = {
    "micro_sol": Path(os.environ.get("SOLBOT_MICRO_STATUS_PATH", "/var/lib/sol-bot/sol-micro-status.json")),
    "micro_sui": Path("/var/lib/sol-bot/sui-micro-status.json"),
    "shadow": Path("/var/lib/sol-bot/sol-shadow-status.json"),
    "r2": Path("/var/lib/sol-bot/r2/status.json"),
    "guardian": Path("/var/lib/sol-bot/guardian-status.json"),
    "execution": Path("/var/lib/sol-bot-execution/status.json"),
    "sui_strategy": Path("/var/lib/sol-bot/sui-strategy-status.json"),
}


def _fmt_ts(ms: int | None) -> str:
    if not ms:
        return "—"
    try:
        dt = datetime.fromtimestamp(int(ms) / 1000.0, tz=_BJT)
        return dt.strftime("%m-%d %H:%M:%S")
    except Exception:
        return "—"


def _read_json_file(path: Path | None) -> dict[str, Any] | None:
    if path is None or not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, TypeError):
        return None


class OpsAgentClient:
    """AF_UNIX client communicating with solbot-ops-agent."""

    def __init__(self, socket_path: str = "/run/solbot-ops/ops.sock", expected_uid: int | None = 0, timeout: float = 5.0) -> None:
        self.socket_path = socket_path
        self.expected_uid = expected_uid
        self.timeout = timeout

    def call(self, payload: dict[str, Any], timeout: float | None = None) -> dict[str, Any] | None:
        if not self.socket_path or not os.path.exists(self.socket_path):
            return None
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(self.timeout if timeout is None else float(timeout))
                sock.connect(self.socket_path)
                if self.expected_uid is not None and hasattr(socket, "SO_PEERCRED"):
                    _, uid, _ = struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                    if uid != self.expected_uid:
                        return {"ok": False, "error": "OPS_AGENT_UID_MISMATCH"}
                sock.sendall(json.dumps(payload).encode("utf-8") + b"\n")
                buf = b""
                while b"\n" not in buf:
                    chunk = sock.recv(8192)
                    if not chunk:
                        break
                    buf += chunk
                    if len(buf) > 524288:
                        return {"ok": False, "error": "OPS_RESPONSE_TOO_LARGE"}
                if not buf:
                    return None
                return json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))
        except (OSError, ValueError, TypeError):
            return None


class OpsMonitor:
    """Combines OpsAgent metrics/actions with read-only SOL-bot status files & API audit DBs."""

    def __init__(
        self,
        *,
        auth_db: Path | None = None,
        notifications_db: Path | None = None,
        status_paths: dict[str, Path] | None = None,
        agent_client: OpsAgentClient | None = None,
        clock=None,
        cache_ttl_sec: float = 2.5,
    ) -> None:
        self.auth_db = auth_db
        self.notifications_db = notifications_db
        self.status_paths = dict(DEFAULT_STATUS_PATHS)
        if status_paths:
            self.status_paths.update(status_paths)
        self.agent = agent_client or OpsAgentClient()
        self.clock = clock or (lambda: time.time_ns() // 1000000)
        self.cache_ttl_sec = cache_ttl_sec
        self._lock = threading.Lock()
        self._cached_at = 0.0
        self._cached_overview: dict[str, Any] | None = None
        self._startup_ms = int(self.clock())
        self._fault_streaks: dict[tuple[str, str, str], int] = {}
        self._fault_startup_grace_ms = 90_000
        self._fault_confirm_ticks = 2

    def _read_sqlite(self, path: Path | None, sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        if path is None or not path.is_file():
            return []
        uri = path.resolve().as_uri()
        for mode in (uri + "?mode=ro", uri + "?mode=ro&immutable=1"):
            try:
                with closing(sqlite3.connect(mode, uri=True, timeout=1.5)) as db:
                    db.row_factory = sqlite3.Row
                    db.execute("PRAGMA query_only=ON")
                    return [dict(r) for r in db.execute(sql, args)]
            except sqlite3.OperationalError:
                continue
        return []

    def _api_security_summary(self, now_ms: int) -> dict[str, Any]:
        from .notifications import _describe_event

        since = now_ms - 86_400_000
        rows = self._read_sqlite(
            self.notifications_db,
            "SELECT event_id, created_at, symbol, details FROM notification_events WHERE kind='SECURITY_ALERT' AND created_at>=? ORDER BY sequence DESC",
            (since,),
        )
        alerts_24h = 0
        latest_alert_at = 0
        recent_alerts: list[dict[str, Any]] = []
        for row in rows:
            try:
                det = json.loads(row.get("details") or "{}")
            except ValueError:
                det = {}
            code = det.get("security_code") or "UNAUTHORIZED_DEVICE"
            if code == "SSH_DESKTOP_LOGIN":
                continue
            alerts_24h += 1
            created_at = int(row.get("created_at") or 0)
            if created_at > latest_alert_at:
                latest_alert_at = created_at
            if len(recent_alerts) < 15:
                title, body, _, _ = _describe_event("SECURITY_ALERT", row.get("symbol"), det)
                recent_alerts.append(
                    {
                        "id": str(row.get("event_id") or f"sec:{created_at}:{alerts_24h}"),
                        "created_at": created_at,
                        "time_text": _fmt_ts(created_at),
                        "level": "warn",
                        "code": code,
                        "channel": det.get("security_channel") or "HTTPS_API",
                        "source_ip": det.get("source_ip") or "未知IP",
                        "title": title,
                        "message": body,
                    }
                )
        auth_fail_rows = self._read_sqlite(
            self.auth_db,
            "SELECT COUNT(*) AS n FROM auth_audit WHERE result NOT IN ('OK','AUTHENTICATED','PERMANENT') AND created_at>=?",
            (since,),
        )
        auth_fails = int(auth_fail_rows[0]["n"]) if auth_fail_rows else 0
        return {
            "api_alerts_24h": alerts_24h,
            "api_auth_failures_24h": auth_fails,
            "latest_alert_at": latest_alert_at,
            "recent_alerts": recent_alerts,
        }

    def _api_logs(self, only_alerts: bool, limit: int) -> dict[str, Any]:
        limit = max(10, min(200, int(limit)))
        entries: list[dict[str, str]] = []
        sec_rows = self._read_sqlite(
            self.notifications_db,
            "SELECT created_at, symbol, details FROM notification_events WHERE kind='SECURITY_ALERT' ORDER BY sequence DESC LIMIT ?",
            (limit,),
        )
        for row in sec_rows:
            try:
                det = json.loads(row.get("details") or "{}")
            except ValueError:
                det = {}
            ch = det.get("security_channel") or "HTTPS_API"
            code = det.get("security_code") or "SECURITY_ALERT"
            ip = det.get("source_ip") or "unknown"
            lvl = "info" if code == "SSH_DESKTOP_LOGIN" else "alert"
            if only_alerts and lvl == "info":
                continue
            entries.append(
                {
                    "level": lvl,
                    "text": f"{_fmt_ts(row.get('created_at'))} [{ch}] {code} src={ip}",
                }
            )
        audit_rows = self._read_sqlite(
            self.auth_db,
            "SELECT created_at, device_id, action, result FROM auth_audit ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        for row in audit_rows:
            res = str(row.get("result") or "")
            lvl = "info" if res in ("OK", "AUTHENTICATED", "PERMANENT") else "alert"
            if only_alerts and lvl == "info":
                continue
            dev = str(row.get("device_id") or "")[:10]
            entries.append(
                {
                    "level": lvl,
                    "text": f"{_fmt_ts(row.get('created_at'))} [AUTH] action={row.get('action')} dev={dev} result={res}",
                }
            )
        entries.sort(key=lambda item: item["text"], reverse=True)
        entries = entries[:limit]
        alert_count = sum(1 for e in entries if e["level"] in ("alert", "warn"))
        return {
            "ok": True,
            "source": "api",
            "count": len(entries),
            "alert_count": alert_count,
            "entries": entries,
        }

    def _bot_modules(
        self,
        now_ms: int,
        services: list[dict[str, Any]],
        live_status_doc: dict[str, Any] | None = None,
        fcm_enabled: bool | None = None,
    ) -> dict[str, Any]:
        svc_map = {s["unit"]: s for s in services}

        def svc_up(unit: str) -> bool | None:
            if unit not in svc_map:
                return None
            return bool(svc_map[unit].get("active"))

        def age_text(ts: Any) -> str:
            if not isinstance(ts, (int, float)):
                return "—"
            sec = max(0, (now_ms - int(ts)) // 1000)
            return f"{sec}s前" if sec < 60 else f"{sec // 60}m前"

        micro_sol = _read_json_file(self.status_paths.get("micro_sol")) or {}
        shadow_st = _read_json_file(self.status_paths.get("shadow")) or {}
        r2_st = _read_json_file(self.status_paths.get("r2")) or {}
        guardian_st = _read_json_file(self.status_paths.get("guardian")) or {}
        exec_st = _read_json_file(self.status_paths.get("execution")) or {}
        micro_sui = _read_json_file(self.status_paths.get("micro_sui")) or {}
        sui_strat = _read_json_file(self.status_paths.get("sui_strategy")) or {}

        # Layer 1: Market & Micro
        micro_ts = micro_sol.get("received_time_ms") or micro_sol.get("feature_generated_time_ms")
        micro_age_ms = max(0, now_ms - int(micro_ts)) if isinstance(micro_ts, (int, float)) else None
        market_svc_ok = svc_up("solbot-market.service")
        micro_svc_ok = svc_up("solbot-micro-sol.service")
        market_healthy = (market_svc_ok is not False) and (micro_svc_ok is not False) and bool(micro_sol.get("ready"))
        layers = [
            {
                "id": "market",
                "title": "行情采集",
                "subtitle": "Market & Micro",
                "healthy": market_healthy,
                "status_text": "正常" if market_healthy else "待同步",
                "metric_text": f"盘口 {micro_age_ms}ms" if micro_age_ms is not None else "K线同步",
                "sub_text": f"Bid {micro_sol.get('bid') or '—'} · 24h流",
            }
        ]

        # Layer 2: Strategy Engines (P3 + R2)
        p3_ok = (shadow_st.get("status") == "ready") and not shadow_st.get("error")
        r2_ok = (r2_st.get("status") == "ready") and not r2_st.get("error")
        r2_stage = ((r2_st.get("candidate") or {}) if isinstance(r2_st.get("candidate"), dict) else {}).get("state") or "IDLE"
        strat_svc_ok = (svc_up("sol-shadow.service") is not False) and (svc_up("sol-r2-shadow.service") is not False)
        strat_healthy = strat_svc_ok and (p3_ok or r2_ok)
        layers.append(
            {
                "id": "strategy",
                "title": "策略引擎",
                "subtitle": "T1 / T2 / R2",
                "healthy": strat_healthy,
                "status_text": "运行中" if strat_healthy else "异常",
                "metric_text": f"R2 {r2_stage}",
                "sub_text": f"P3 {'就绪' if p3_ok else '等待'} · 心跳 {age_text(shadow_st.get('updated_at'))}",
            }
        )

        # Layer 3: Guardian P4 Risk
        g_live_state = str(guardian_st.get("live_state") or (live_status_doc or {}).get("state") or "LIVE_OFF")
        g_locked = bool(guardian_st.get("locked")) or g_live_state == "HARD_LOCKED"
        g_svc_ok = (svc_up("solbot-guardian.service") is not False) and (svc_up("solbot-guardian-ipc.service") is not False)
        g_healthy = g_svc_ok and not g_locked
        layers.append(
            {
                "id": "risk",
                "title": "独立风控",
                "subtitle": "Guardian P4",
                "healthy": g_healthy,
                "status_text": "硬锁熔断" if g_locked else ("正常" if g_healthy else "离线"),
                "metric_text": g_live_state,
                "sub_text": f"心跳 {age_text(guardian_st.get('at'))} · IPC 守卫",
            }
        )

        # Layer 4: Execution P5 & App Gateway
        p5_state = str(exec_st.get("live_state") or g_live_state or "LIVE_OFF")
        p5_svc_ok = (svc_up("solbot-execution-p5.service") is not False) and (svc_up("nginx.service") is not False)
        exec_healthy = p5_svc_ok
        fcm_label = "FCM ON" if fcm_enabled else "API 在线"
        layers.append(
            {
                "id": "execution",
                "title": "执行网关",
                "subtitle": "Exec P5 & API",
                "healthy": exec_healthy,
                "status_text": "在线" if exec_healthy else "降级",
                "metric_text": p5_state,
                "sub_text": f"挂单 {exec_st.get('real_orders', 0)} · {fcm_label}",
            }
        )

        # Enrich individual services with live telemetry
        enriched_services: list[dict[str, Any]] = []
        detail_lookup = {
            "solbot-market.service": "SOLUSDT 1m-4h K线采集",
            "solbot-micro-sol.service": f"盘口深度 · {micro_age_ms}ms" if micro_age_ms is not None else "盘口深度流",
            "sol-shadow.service": f"P3 {'就绪' if p3_ok else '等待'} · {age_text(shadow_st.get('updated_at'))}",
            "sol-r2-shadow.service": f"状态 {r2_stage} · {age_text(r2_st.get('updated_at'))}",
            "solbot-guardian.service": f"{g_live_state} · {age_text(guardian_st.get('at'))}",
            "solbot-guardian-ipc.service": "Unix Socket 风控授权守卫",
            "solbot-execution-p5.service": f"门禁 {p5_state} · {age_text(exec_st.get('at'))}",
            "solbot-app.service": f"端口 8016 · {fcm_label}",
            "nginx.service": "端口 443 TLS/WSS 反代",
            "solbot-sui-market.service": "SUIUSDT 公开K线研究采集",
            "solbot-micro-sui.service": f"SUI Bid {micro_sui.get('bid') or '—'}",
            "sui-strategy.service": f"SUI {sui_strat.get('status') or 'ready'} · {age_text(sui_strat.get('updated_at'))}",
        }
        for s in services:
            item = dict(s)
            item["detail"] = detail_lookup.get(s["unit"], s.get("sub_state") or "")
            enriched_services.append(item)

        sol_services = [s for s in enriched_services if s.get("group") != "sui_research"]
        sui_services = [s for s in enriched_services if s.get("group") == "sui_research"]
        active_count = sum(1 for s in enriched_services if s.get("active"))
        sol_active = sum(1 for s in sol_services if s.get("active"))
        sui_active = sum(1 for s in sui_services if s.get("active"))
        all_layers_ok = all(layer["healthy"] for layer in layers)

        return {
            "overall_healthy": all_layers_ok and (not enriched_services or sol_active == len(sol_services)),
            "active_count": active_count,
            "total_count": len(enriched_services),
            "sol_active_count": sol_active,
            "sol_total_count": len(sol_services),
            "sui_active_count": sui_active,
            "sui_total_count": len(sui_services),
            "mode_summary": f"P3+R2 Shadow · {p5_state}",
            "layers": layers,
            "services": enriched_services,
        }

    @staticmethod
    def _is_recent_health_fail(entry: dict[str, Any] | None, now_ms: int, max_age_ms: int = 900_000) -> bool:
        if not isinstance(entry, dict):
            return False
        text = str(entry.get("text") or "")
        if "status=fail" not in text:
            return False
        token = text.split(None, 1)[0]
        try:
            dt = datetime.fromisoformat(token)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=_BJT)
            ts_ms = int(dt.timestamp() * 1000)
            return 0 <= (now_ms - ts_ms) <= max_age_ms
        except ValueError:
            return False

    def _collect_faults(
        self,
        now_ms: int,
        *,
        agent_ok: bool,
        raw_metrics: dict[str, Any],
        services: list[dict[str, Any]],
        enforce_heartbeats: bool = False,
        health_entry: dict[str, Any] | None = None,
    ) -> list[dict[str, str]]:
        """Collect active Category-1 urgent faults (Server faults + SOL-bot service faults)."""
        faults: list[dict[str, str]] = []
        if not agent_ok:
            faults.append(
                {
                    "kind": "SERVER_ERROR",
                    "fault_code": "OPS_AGENT_DOWN",
                    "fault_unit": "ops-agent",
                    "summary": "Ops 运维代理无响应",
                }
            )
            return faults

        disk_pct = raw_metrics.get("disk_pct")
        if isinstance(disk_pct, (int, float)) and disk_pct >= 92.0:
            faults.append(
                {
                    "kind": "SERVER_ERROR",
                    "fault_code": "DISK_CRITICAL",
                    "fault_unit": "disk",
                    "summary": f"磁盘使用率 {disk_pct:.1f}% (≥92%)",
                }
            )

        ram_pct = raw_metrics.get("ram_pct")
        ram_avail = raw_metrics.get("ram_available_mb")
        if (isinstance(ram_pct, (int, float)) and ram_pct >= 95.0) or (
            isinstance(ram_avail, (int, float)) and ram_avail < 120
        ):
            faults.append(
                {
                    "kind": "SERVER_ERROR",
                    "fault_code": "RAM_CRITICAL",
                    "fault_unit": "memory",
                    "summary": f"内存告急 ({ram_pct}% / 可用 {ram_avail} MB)",
                }
            )

        if self._is_recent_health_fail(health_entry, now_ms):
            faults.append(
                {
                    "kind": "SERVER_ERROR",
                    "fault_code": "HEALTHCHECK_FAIL",
                    "fault_unit": "health",
                    "summary": "health.log 巡检出现 status=fail",
                }
            )

        systemctl_available = any(
            (s.get("active_state") or ("active" if s.get("active") else "inactive")) != "unknown"
            for s in services
        )
        if systemctl_available:
            for s in services:
                if s.get("active", True):
                    continue
                unit = str(s.get("unit") or "system")
                label = str(s.get("label") or unit)
                if unit == "nginx.service":
                    faults.append(
                        {
                            "kind": "SERVER_ERROR",
                            "fault_code": "NGINX_DOWN",
                            "fault_unit": "nginx.service",
                            "summary": "Nginx TLS 网关已停止",
                        }
                    )
                else:
                    faults.append(
                        {
                            "kind": "SOLBOT_SERVICE_FAULT",
                            "fault_code": "SERVICE_DOWN",
                            "fault_unit": unit,
                            "summary": f"{label} ({unit}) 已停止",
                        }
                    )

        if enforce_heartbeats:
            hb_specs = (
                ("micro_sol", ("received_time_ms", "feature_generated_time_ms"), 60_000),
                ("shadow", ("updated_at",), 180_000),
                ("r2", ("updated_at",), 180_000),
                ("guardian", (), 0),
                ("execution", (), 0),
            )
            for key, ts_fields, max_age_ms in hb_specs:
                st = _read_json_file(self.status_paths.get(key))
                if not st:
                    continue
                if st.get("error"):
                    faults.append(
                        {
                            "kind": "SOLBOT_SERVICE_FAULT",
                            "fault_code": "MODULE_ERROR",
                            "fault_unit": key,
                            "summary": f"模块 {key} 状态报错: {st.get('error')}",
                        }
                    )
                    continue
                if max_age_ms > 0:
                    ts = next((st.get(f) for f in ts_fields if isinstance(st.get(f), (int, float))), None)
                    if ts is not None and (now_ms - int(ts)) > max_age_ms:
                        age_sec = max(0, (now_ms - int(ts)) // 1000)
                        faults.append(
                            {
                                "kind": "SOLBOT_SERVICE_FAULT",
                                "fault_code": "HEARTBEAT_STALE",
                                "fault_unit": key,
                                "summary": f"模块 {key} 心跳超时 ({age_sec}s)",
                            }
                        )
        return faults

    def check_critical_faults(self, notifications: Any, *, cooldown_ms: int = 300_000) -> list[dict[str, str]]:
        """Probe critical server & SOL-bot faults with 2-check debounce and emit urgent alerts."""
        if notifications is None or not callable(getattr(notifications, "publish_fault_alert", None)):
            return []
        now_ms = int(self.clock())
        raw_metrics = self.agent.call({"action": "metrics"})
        agent_ok = bool(raw_metrics and raw_metrics.get("ok"))
        services = raw_metrics.get("services", []) if agent_ok else []
        health_entry: dict[str, Any] | None = None
        if agent_ok:
            h_res = self.agent.call({"action": "logs", "source": "health", "only_alerts": False, "limit": 1})
            if isinstance(h_res, dict) and h_res.get("ok") and isinstance(h_res.get("entries"), list) and h_res["entries"]:
                health_entry = h_res["entries"][0]
        past_grace = (now_ms - self._startup_ms) >= self._fault_startup_grace_ms
        faults = self._collect_faults(
            now_ms,
            agent_ok=agent_ok,
            raw_metrics=raw_metrics or {},
            services=services,
            enforce_heartbeats=past_grace,
            health_entry=health_entry,
        )
        active_map = {(f["kind"], f["fault_code"], f["fault_unit"]): f for f in faults}
        for k in list(self._fault_streaks):
            if k not in active_map:
                self._fault_streaks.pop(k, None)
        triggered: list[dict[str, str]] = []
        for key, fault in active_map.items():
            streak = self._fault_streaks.get(key, 0) + 1
            self._fault_streaks[key] = streak
            required_ticks = 3 if fault["fault_code"] == "OPS_AGENT_DOWN" else self._fault_confirm_ticks
            if streak >= required_ticks:
                try:
                    if notifications.publish_fault_alert(
                        fault["kind"],
                        fault["fault_code"],
                        fault["fault_unit"],
                        cooldown_ms=cooldown_ms,
                    ):
                        triggered.append(fault)
                except Exception:
                    pass
        return triggered

    def overview(self, *, cached: bool = False, live_status_doc: dict[str, Any] | None = None, fcm_enabled: bool | None = None) -> dict[str, Any]:
        now_s = time.monotonic()
        if cached:
            with self._lock:
                if self._cached_overview is not None and (now_s - self._cached_at) <= self.cache_ttl_sec:
                    return self._cached_overview

        now_ms = int(self.clock())
        raw_metrics = self.agent.call({"action": "metrics"})
        agent_ok = bool(raw_metrics and raw_metrics.get("ok"))
        services = raw_metrics.get("services", []) if agent_ok else []
        sec_raw = raw_metrics.get("security", {}) if agent_ok else {}
        api_sec = self._api_security_summary(now_ms)

        ssh_active = int(sec_raw.get("ssh_active_count", 0))
        ssh_failed = int(sec_raw.get("ssh_failed_recent", 0))
        f2b_banned = int(sec_raw.get("fail2ban_currently_banned", 0))
        f2b_total_failed = int(sec_raw.get("fail2ban_total_failed", 0))
        api_alerts = int(api_sec.get("api_alerts_24h", 0))
        api_fails = int(api_sec.get("api_auth_failures_24h", 0))
        latest_alert_at = int(api_sec.get("latest_alert_at", 0))
        recent_alerts = api_sec.get("recent_alerts", [])

        past_grace = (now_ms - self._startup_ms) >= self._fault_startup_grace_ms
        faults = self._collect_faults(
            now_ms,
            agent_ok=agent_ok,
            raw_metrics=raw_metrics or {},
            services=services,
            enforce_heartbeats=past_grace,
        )
        has_server_fault = any(f["kind"] == "SERVER_ERROR" for f in faults)
        fault_label = "服务器故障" if has_server_fault else ("SOL-bot故障" if faults else "")
        fault_summary = "；".join(f["summary"] for f in faults[:2])

        if faults:
            sec_status = "FAULT"
            sec_label = fault_label
        else:
            sec_status = "OK"
            sec_label = "安全正常"

        server_doc = {
            "available": True,
            "ops_agent_connected": agent_ok,
            "hostname": raw_metrics.get("hostname", "solbot-node") if agent_ok else "solbot-node",
            "cpu_pct": raw_metrics.get("cpu_pct", 0.0) if agent_ok else None,
            "cpu_cores": raw_metrics.get("cpu_cores", 2) if agent_ok else None,
            "load_1m": raw_metrics.get("load_1m") if agent_ok else None,
            "load_5m": raw_metrics.get("load_5m") if agent_ok else None,
            "uptime_text": raw_metrics.get("uptime_text", "—") if agent_ok else "—",
            "ram_total_mb": raw_metrics.get("ram_total_mb") if agent_ok else None,
            "ram_used_mb": raw_metrics.get("ram_used_mb") if agent_ok else None,
            "ram_available_mb": raw_metrics.get("ram_available_mb") if agent_ok else None,
            "ram_pct": raw_metrics.get("ram_pct") if agent_ok else None,
            "buff_cache_mb": raw_metrics.get("buff_cache_mb") if agent_ok else None,
            "swap_total_mb": raw_metrics.get("swap_total_mb") if agent_ok else None,
            "swap_used_mb": raw_metrics.get("swap_used_mb") if agent_ok else None,
            "swap_pct": raw_metrics.get("swap_pct") if agent_ok else None,
            "disk_total_gb": raw_metrics.get("disk_total_gb") if agent_ok else None,
            "disk_used_gb": raw_metrics.get("disk_used_gb") if agent_ok else None,
            "disk_free_gb": raw_metrics.get("disk_free_gb") if agent_ok else None,
            "disk_pct": raw_metrics.get("disk_pct") if agent_ok else None,
            "processes_total": raw_metrics.get("processes_total") if agent_ok else None,
            "services_active": raw_metrics.get("services_active") if agent_ok else None,
            "services_total": raw_metrics.get("services_total") if agent_ok else None,
            "cleanable_mb": raw_metrics.get("cleanable_mb", 0.0) if agent_ok else 0.0,
            "cleanable_items": raw_metrics.get("cleanable_items", []) if agent_ok else [],
            "security": {
                "status": sec_status,
                "status_label": sec_label,
                "critical_fault": bool(faults),
                "fault_label": fault_label,
                "fault_summary": fault_summary,
                "critical_faults": faults,
                "latest_alert_at": latest_alert_at,
                "recent_alerts": recent_alerts,
                "ssh_active_count": ssh_active,
                "ssh_sessions": sec_raw.get("ssh_sessions", []),
                "ssh_failed_recent": ssh_failed,
                "last_failed_ip": sec_raw.get("last_failed_ip"),
                "last_accepted_ip": sec_raw.get("last_accepted_ip"),
                "fail2ban_currently_failed": sec_raw.get("fail2ban_currently_failed", 0),
                "fail2ban_total_failed": f2b_total_failed,
                "fail2ban_currently_banned": f2b_banned,
                "fail2ban_banned_ips": sec_raw.get("fail2ban_banned_ips", []),
                "api_alerts_24h": api_alerts,
                "api_auth_failures_24h": api_fails,
                "summary_text": f"⚠️ {fault_summary}" if faults else f"SSH 在线 {ssh_active} · 拦截 {max(ssh_failed, f2b_total_failed)} · 静默安全记录 {api_alerts}",
            },
        }

        bot_doc = self._bot_modules(now_ms, services, live_status_doc=live_status_doc, fcm_enabled=fcm_enabled)
        payload = {
            "available": True,
            "server": server_doc,
            "bot_modules": bot_doc,
            "updated_at": now_ms,
        }
        with self._lock:
            self._cached_at = time.monotonic()
            self._cached_overview = payload
        return payload

    def logs(self, source: str = "auth", only_alerts: bool = False, limit: int = 80) -> dict[str, Any]:
        if source == "api":
            return self._api_logs(only_alerts=only_alerts, limit=limit)
        if source not in ("auth", "fail2ban", "health", "journal"):
            raise ValueError("INVALID_LOG_SOURCE")
        res = self.agent.call({"action": "logs", "source": source, "only_alerts": bool(only_alerts), "limit": int(limit)})
        if not res:
            return {"ok": False, "source": source, "error": "OPS_AGENT_UNAVAILABLE", "entries": [], "count": 0, "alert_count": 0}
        return res

    def optimize_memory(self) -> dict[str, Any]:
        res = self.agent.call({"action": "optimize_memory"})
        if not res:
            return {"ok": False, "error": "OPS_AGENT_UNAVAILABLE", "message": "本地运维代理未连接"}
        with self._lock:
            self._cached_overview = None
        return res

    def clean_cache(self, dry_run: bool = True) -> dict[str, Any]:
        res = self.agent.call({"action": "clean_cache", "dry_run": bool(dry_run)})
        if not res:
            return {"ok": False, "error": "OPS_AGENT_UNAVAILABLE", "message": "本地运维代理未连接"}
        if not dry_run:
            with self._lock:
                self._cached_overview = None
        return res

    def reset_shadow_data(self) -> dict[str, Any]:
        res = self.agent.call({"action": "reset_shadow_data"}, timeout=20.0)
        if not res:
            return {"ok": False, "error": "OPS_AGENT_UNAVAILABLE", "message": "本地运维代理未连接"}
        with self._lock:
            self._cached_overview = None
        return res
