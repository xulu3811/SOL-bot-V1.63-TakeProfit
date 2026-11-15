"""Whitelisted AF_UNIX operations agent for SOL-bot server observability & safe maintenance."""
from __future__ import annotations

import argparse
import glob
import json
import os
try:
    import pwd
except ImportError:  # Windows test host
    pwd = None  # type: ignore[assignment]
import re
import shutil
import socket
import stat
import subprocess
import time
from pathlib import Path
from typing import Any

SERVICE_SPECS = (
    {"unit": "solbot-market.service", "label": "SOL K线采集", "group": "market", "group_label": "行情采集"},
    {"unit": "solbot-micro-sol.service", "label": "SOL 盘口深度", "group": "market", "group_label": "行情采集"},
    {"unit": "sol-shadow.service", "label": "T1/T2/R1 影子引擎", "group": "strategy", "group_label": "策略引擎"},
    {"unit": "sol-r2-shadow.service", "label": "R2 反弹状态机", "group": "strategy", "group_label": "策略引擎"},
    {"unit": "solbot-guardian.service", "label": "Guardian P4 风控", "group": "risk", "group_label": "独立风控"},
    {"unit": "solbot-guardian-ipc.service", "label": "Guardian IPC", "group": "risk", "group_label": "独立风控"},
    {"unit": "solbot-execution-p5.service", "label": "P5 执行门禁", "group": "execution", "group_label": "执行与网关"},
    {"unit": "solbot-app.service", "label": "App API & FCM", "group": "execution", "group_label": "执行与网关"},
    {"unit": "nginx.service", "label": "Nginx TLS 网关", "group": "execution", "group_label": "执行与网关"},
    {"unit": "solbot-sui-market.service", "label": "SUI K线采集", "group": "sui_research", "group_label": "SUI 辅助研究"},
    {"unit": "solbot-micro-sui.service", "label": "SUI 盘口深度", "group": "sui_research", "group_label": "SUI 辅助研究"},
    {"unit": "sui-strategy.service", "label": "SUI 数学研究", "group": "sui_research", "group_label": "SUI 辅助研究"},
)

ALLOWED_SQLITE_KEEP_SUFFIXES = frozenset({"wal", "shm", "journal", "lock", "bak"})
SQLITE_TMP_SCAN_DIRS = (Path("/var/lib/sol-bot/r2"), Path("/var/lib/sol-bot/database"))
LOG_FILES = {
    "auth": Path("/var/log/auth.log"),
    "fail2ban": Path("/var/log/fail2ban.log"),
    "health": Path("/var/log/sol-bot/audit/health.log"),
}
OPTIMIZE_COOLDOWN_SEC = 60
ORPHAN_MIN_AGE_SEC = 600
TMP_MAX_AGE_SEC = 3 * 86400

_SECRET_RE = re.compile(
    r"(?i)(password|passphrase|secret|token|authorization|api[_-]?key)\s*[:=]\s*\S+"
)
_ALERT_RE = re.compile(
    r"(?i)(failed password|invalid user|authentication failure|error|ban\b|unban|warning|status=warn|status=fail|break-in|refused|denied)"
)


def parse_meminfo(text: str) -> dict[str, int]:
    """Parse /proc/meminfo key-values into KiB."""
    values: dict[str, int] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, rest = line.split(":", 1)
        parts = rest.strip().split()
        if not parts:
            continue
        try:
            values[key.strip()] = int(parts[0])
        except ValueError:
            continue
    total_kb = values.get("MemTotal", 0)
    avail_kb = values.get("MemAvailable", values.get("MemFree", 0))
    used_kb = max(0, total_kb - avail_kb)
    buff_cache_kb = values.get("Buffers", 0) + values.get("Cached", 0) + values.get("SReclaimable", 0)
    swap_total_kb = values.get("SwapTotal", 0)
    swap_free_kb = values.get("SwapFree", 0)
    swap_used_kb = max(0, swap_total_kb - swap_free_kb)
    return {
        "ram_total_mb": total_kb // 1024,
        "ram_used_mb": used_kb // 1024,
        "ram_available_mb": avail_kb // 1024,
        "ram_pct": round((used_kb / total_kb) * 100.0, 1) if total_kb > 0 else 0.0,
        "buff_cache_mb": buff_cache_kb // 1024,
        "swap_total_mb": swap_total_kb // 1024,
        "swap_used_mb": swap_used_kb // 1024,
        "swap_pct": round((swap_used_kb / swap_total_kb) * 100.0, 1) if swap_total_kb > 0 else 0.0,
    }


def _read_proc_cpu_totals(proc_stat_path: Path = Path("/proc/stat")) -> tuple[int, int] | None:
    try:
        first = proc_stat_path.read_text(encoding="utf-8", errors="replace").splitlines()[0]
    except (OSError, IndexError):
        return None
    parts = first.split()
    if len(parts) < 5 or parts[0] != "cpu":
        return None
    nums = []
    for item in parts[1:11]:
        try:
            nums.append(int(item))
        except ValueError:
            nums.append(0)
    total = sum(nums)
    idle = nums[3] + (nums[4] if len(nums) > 4 else 0)
    return total, idle


def format_uptime(seconds: float) -> str:
    sec = max(0, int(seconds))
    days, rem = divmod(sec, 86400)
    hours, rem = divmod(rem, 3600)
    mins, _ = divmod(rem, 60)
    if days > 0:
        return f"{days}天{hours}小时"
    if hours > 0:
        return f"{hours}小时{mins}分"
    return f"{mins}分钟"


def mask_log_line(line: str) -> str:
    return _SECRET_RE.sub(r"\1=***", line.rstrip("\r\n"))


def classify_log_level(line: str) -> str:
    if _ALERT_RE.search(line):
        if re.search(r"(?i)(status=warn|warning|notice|unban)", line):
            return "warn"
        return "alert"
    return "info"


def tail_lines(path: Path, limit: int) -> list[str]:
    if not path.is_file():
        return []
    # Read up to last 256 KB to keep memory bounded
    size = path.stat().st_size
    read_size = min(size, 262144)
    with path.open("rb") as fh:
        if size > read_size:
            fh.seek(size - read_size)
        raw = fh.read().decode("utf-8", errors="replace")
    lines = raw.splitlines()
    if size > read_size and lines:
        lines = lines[1:]
    return lines[-limit:]


def parse_auth_summary(lines: list[str]) -> dict[str, Any]:
    failed = 0
    invalid_users = 0
    accepted = 0
    last_failed_ip = ""
    last_accepted_ip = ""
    ip_re = re.compile(r"\bfrom\s+([0-9a-fA-F:.]+)\b")
    for line in lines:
        low = line.lower()
        if "failed password" in low or "authentication failure" in low:
            failed += 1
            m = ip_re.search(line)
            if m:
                last_failed_ip = m.group(1)
        elif "invalid user" in low:
            invalid_users += 1
            m = ip_re.search(line)
            if m:
                last_failed_ip = m.group(1)
        elif "accepted " in low and "sshd" in low:
            accepted += 1
            m = ip_re.search(line)
            if m:
                last_accepted_ip = m.group(1)
    return {
        "ssh_failed_recent": failed + invalid_users,
        "ssh_accepted_recent": accepted,
        "last_failed_ip": last_failed_ip or None,
        "last_accepted_ip": last_accepted_ip or None,
    }


def parse_fail2ban_output(text: str) -> dict[str, Any]:
    cur_failed = 0
    total_failed = 0
    cur_banned = 0
    total_banned = 0
    banned_ips: list[str] = []
    for line in text.splitlines():
        if "Currently failed:" in line:
            cur_failed = int(line.split(":")[-1].strip() or 0)
        elif "Total failed:" in line:
            total_failed = int(line.split(":")[-1].strip() or 0)
        elif "Currently banned:" in line:
            cur_banned = int(line.split(":")[-1].strip() or 0)
        elif "Total banned:" in line:
            total_banned = int(line.split(":")[-1].strip() or 0)
        elif "Banned IP list:" in line:
            raw = line.split(":", 1)[-1].strip()
            banned_ips = [ip for ip in raw.split() if ip]
    return {
        "currently_failed": cur_failed,
        "total_failed": total_failed,
        "currently_banned": cur_banned,
        "total_banned": total_banned,
        "banned_ips": banned_ips[:20],
    }


def is_orphan_sqlite_temp(path: Path, now_sec: float | None = None) -> bool:
    name = path.name
    if ".sqlite." not in name:
        return False
    suffix = name.rsplit(".sqlite.", 1)[-1].lower()
    if not suffix or suffix in ALLOWED_SQLITE_KEEP_SUFFIXES:
        return False
    # Handle suffix like `doho8fo5-journal` -> check base random tag too
    if "-" in suffix:
        base, tail = suffix.split("-", 1)
        if not base or tail not in ALLOWED_SQLITE_KEEP_SUFFIXES:
            return False
    try:
        st = path.stat()
    except OSError:
        return False
    if not stat.S_ISREG(st.st_mode):
        return False
    cur = time.time() if now_sec is None else now_sec
    return (cur - st.st_mtime) >= ORPHAN_MIN_AGE_SEC


def _is_file_in_use(path: Path) -> bool:
    try:
        proc = subprocess.run(
            ["fuser", str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=2,
            check=False,
        )
        return proc.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return True  # fail-safe: assume in use if fuser cannot run


def _read_pid_rss_mb(pid: int) -> float | None:
    if pid <= 0:
        return None
    status_path = Path(f"/proc/{pid}/status")
    try:
        for line in status_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("VmRSS:"):
                kb = int(line.split()[1])
                return round(kb / 1024.0, 1)
    except (OSError, ValueError, IndexError):
        return None
    return None


class OpsBackend:
    """Collects server/service metrics & runs strictly whitelisted maintenance actions."""

    def __init__(
        self,
        *,
        drop_caches_path: Path = Path("/proc/sys/vm/drop_caches"),
        meminfo_path: Path = Path("/proc/meminfo"),
        proc_stat_path: Path = Path("/proc/stat"),
        uptime_path: Path = Path("/proc/uptime"),
        loadavg_path: Path = Path("/proc/loadavg"),
        sqlite_dirs: tuple[Path, ...] = SQLITE_TMP_SCAN_DIRS,
        apt_cache_dir: Path = Path("/var/cache/apt/archives"),
        tmp_dir: Path = Path("/tmp"),
        log_files: dict[str, Path] | None = None,
        clock=None,
    ) -> None:
        self.drop_caches_path = drop_caches_path
        self.meminfo_path = meminfo_path
        self.proc_stat_path = proc_stat_path
        self.uptime_path = uptime_path
        self.loadavg_path = loadavg_path
        self.sqlite_dirs = sqlite_dirs
        self.apt_cache_dir = apt_cache_dir
        self.tmp_dir = tmp_dir
        self.log_files = log_files or dict(LOG_FILES)
        self.clock = clock or time.time
        self._last_cpu: tuple[int, int] | None = None
        self._last_optimize_ts: float = 0.0

    def _cpu_pct(self) -> float:
        cur = _read_proc_cpu_totals(self.proc_stat_path)
        if cur is None:
            return 0.0
        prev = self._last_cpu
        if prev is None:
            time.sleep(0.12)
            cur2 = _read_proc_cpu_totals(self.proc_stat_path)
            if cur2 is None:
                self._last_cpu = cur
                return 0.0
            prev, cur = cur, cur2
        self._last_cpu = cur
        dt = cur[0] - prev[0]
        di = cur[1] - prev[1]
        if dt <= 0:
            return 0.0
        return round(max(0.0, min(100.0, (dt - di) / dt * 100.0)), 1)

    def _systemd_services(self) -> list[dict[str, Any]]:
        units = [spec["unit"] for spec in SERVICE_SPECS]
        props_by_unit: dict[str, dict[str, str]] = {}
        try:
            proc = subprocess.run(
                [
                    "systemctl",
                    "show",
                    *units,
                    "--property=Id,ActiveState,SubState,MainPID,MemoryCurrent,ExecMainStartTimestamp",
                ],
                capture_output=True,
                text=True,
                timeout=4,
                check=False,
            )
            if proc.returncode == 0:
                current: dict[str, str] = {}
                for line in proc.stdout.splitlines():
                    if not line.strip():
                        if "Id" in current:
                            props_by_unit[current["Id"]] = current
                        current = {}
                        continue
                    if "=" in line:
                        k, v = line.split("=", 1)
                        current[k.strip()] = v.strip()
                if "Id" in current:
                    props_by_unit[current["Id"]] = current
        except (OSError, subprocess.SubprocessError):
            pass

        results: list[dict[str, Any]] = []
        for spec in SERVICE_SPECS:
            unit = spec["unit"]
            props = props_by_unit.get(unit, {})
            active_state = props.get("ActiveState", "unknown")
            sub_state = props.get("SubState", "unknown")
            try:
                pid = int(props.get("MainPID") or 0)
            except ValueError:
                pid = 0
            mem_mb: float | None = None
            raw_mem = props.get("MemoryCurrent", "")
            if raw_mem.isdigit():
                mem_mb = round(int(raw_mem) / (1024.0 * 1024.0), 1)
            if mem_mb is None and pid > 0:
                mem_mb = _read_pid_rss_mb(pid)
            results.append(
                {
                    "unit": unit,
                    "label": spec["label"],
                    "group": spec["group"],
                    "group_label": spec["group_label"],
                    "active": active_state == "active" and sub_state == "running",
                    "active_state": active_state,
                    "sub_state": sub_state,
                    "pid": pid,
                    "memory_mb": mem_mb,
                    "started_at": props.get("ExecMainStartTimestamp") or None,
                }
            )
        return results

    def _ssh_sessions(self) -> list[dict[str, str]]:
        try:
            proc = subprocess.run(["who"], capture_output=True, text=True, timeout=2, check=False)
            if proc.returncode != 0:
                return []
            sessions = []
            for line in proc.stdout.splitlines():
                parts = line.split()
                if len(parts) < 4:
                    continue
                host = parts[-1].strip("()") if parts[-1].startswith("(") else "local"
                sessions.append(
                    {
                        "user": parts[0],
                        "tty": parts[1],
                        "since": f"{parts[2]} {parts[3]}",
                        "host": host,
                    }
                )
            return sessions
        except (OSError, subprocess.SubprocessError):
            return []

    def _fail2ban_sshd(self) -> dict[str, Any]:
        try:
            proc = subprocess.run(
                ["fail2ban-client", "status", "sshd"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            if proc.returncode == 0:
                return parse_fail2ban_output(proc.stdout)
        except (OSError, subprocess.SubprocessError):
            pass
        return {"currently_failed": 0, "total_failed": 0, "currently_banned": 0, "total_banned": 0, "banned_ips": []}

    def _journal_disk_bytes(self) -> int:
        try:
            proc = subprocess.run(
                ["journalctl", "--disk-usage"],
                capture_output=True,
                text=True,
                timeout=3,
                check=False,
            )
            m = re.search(r"take up\s+([0-9.]+)\s*([KMG]?)", proc.stdout)
            if not m:
                return 0
            val = float(m.group(1))
            unit = m.group(2).upper()
            mult = {"K": 1024, "M": 1024**2, "G": 1024**3}.get(unit, 1)
            return int(val * mult)
        except (OSError, subprocess.SubprocessError, ValueError):
            return 0

    def _find_orphan_sqlite_temps(self) -> list[Path]:
        now_sec = self.clock()
        found: list[Path] = []
        for directory in self.sqlite_dirs:
            if not directory.is_dir():
                continue
            for item in directory.iterdir():
                if is_orphan_sqlite_temp(item, now_sec=now_sec):
                    found.append(item)
        return found

    def _find_old_tmp_files(self) -> list[Path]:
        if not self.tmp_dir.is_dir():
            return []
        cutoff = self.clock() - TMP_MAX_AGE_SEC
        found: list[Path] = []
        for item in self.tmp_dir.iterdir():
            name = item.name
            if name.startswith(("systemd-private-", "ssh-", ".X", "solbot")):
                continue
            if name.endswith((".sock", ".pid", ".lock")):
                continue
            try:
                st = item.lstat()
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode) and st.st_mtime < cutoff:
                found.append(item)
        return found

    def _find_apt_debs(self) -> list[Path]:
        if not self.apt_cache_dir.is_dir():
            return []
        out: list[Path] = []
        for pattern in ("*.deb", "partial/*"):
            for p in glob.glob(str(self.apt_cache_dir / pattern)):
                path = Path(p)
                if path.is_file():
                    out.append(path)
        return out

    def clean_cache(self, dry_run: bool = True) -> dict[str, Any]:
        disk_before = shutil.disk_usage("/")
        items: list[dict[str, Any]] = []
        total_freed = 0

        # 1. Systemd journal > 32 MB
        j_bytes = self._journal_disk_bytes()
        j_target = 32 * 1024 * 1024
        j_reclaim = max(0, j_bytes - j_target)
        if dry_run:
            items.append(
                {
                    "id": "journal",
                    "label": "Systemd 日志归档 (>32MB)",
                    "bytes": j_reclaim,
                    "mb": round(j_reclaim / (1024 * 1024), 1),
                }
            )
            total_freed += j_reclaim
        else:
            if j_reclaim > 0:
                subprocess.run(
                    ["journalctl", "--vacuum-size=32M"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                    check=False,
                )
            j_after = self._journal_disk_bytes()
            freed = max(0, j_bytes - j_after)
            items.append(
                {
                    "id": "journal",
                    "label": "Systemd 日志归档 (>32MB)",
                    "bytes": freed,
                    "mb": round(freed / (1024 * 1024), 1),
                }
            )
            total_freed += freed

        # 2. APT package archives
        apt_files = self._find_apt_debs()
        apt_bytes = sum(p.stat().st_size for p in apt_files if p.exists())
        if not dry_run and apt_bytes > 0:
            subprocess.run(
                ["apt-get", "clean"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=15,
                check=False,
            )
            apt_after = sum(p.stat().st_size for p in self._find_apt_debs() if p.exists())
            apt_freed = max(0, apt_bytes - apt_after)
        else:
            apt_freed = apt_bytes
        items.append(
            {
                "id": "apt_cache",
                "label": "APT 软件包缓存",
                "bytes": apt_freed,
                "mb": round(apt_freed / (1024 * 1024), 1),
            }
        )
        total_freed += apt_freed

        # 3. Orphaned SQLite temp files (checked with fuser to ensure zero active handles)
        orphans = self._find_orphan_sqlite_temps()
        orphan_freed = 0
        orphan_count = 0
        for p in orphans:
            try:
                sz = p.stat().st_size
            except OSError:
                continue
            if dry_run:
                orphan_freed += sz
                orphan_count += 1
            else:
                if not _is_file_in_use(p):
                    try:
                        p.unlink()
                        orphan_freed += sz
                        orphan_count += 1
                    except OSError:
                        pass
        items.append(
            {
                "id": "sqlite_orphan_tmp",
                "label": f"孤立 SQLite 临时残留 ({orphan_count} 个)",
                "bytes": orphan_freed,
                "mb": round(orphan_freed / (1024 * 1024), 1),
            }
        )
        total_freed += orphan_freed

        # 4. Old /tmp files (> 3 days)
        old_tmp = self._find_old_tmp_files()
        tmp_freed = 0
        tmp_count = 0
        for p in old_tmp:
            try:
                sz = p.stat().st_size
            except OSError:
                continue
            if dry_run:
                tmp_freed += sz
                tmp_count += 1
            else:
                try:
                    p.unlink()
                    tmp_freed += sz
                    tmp_count += 1
                except OSError:
                    pass
        items.append(
            {
                "id": "tmp_old",
                "label": f"/tmp 过期临时文件 ({tmp_count} 个)",
                "bytes": tmp_freed,
                "mb": round(tmp_freed / (1024 * 1024), 1),
            }
        )
        total_freed += tmp_freed

        disk_after = shutil.disk_usage("/")
        return {
            "ok": True,
            "dry_run": dry_run,
            "items": items,
            "total_freed_bytes": total_freed,
            "total_freed_mb": round(total_freed / (1024 * 1024), 1),
            "disk_before_pct": round(disk_before.used / disk_before.total * 100.0, 1) if disk_before.total else 0.0,
            "disk_after_pct": round(disk_after.used / disk_after.total * 100.0, 1) if disk_after.total else 0.0,
        }

    def optimize_memory(self) -> dict[str, Any]:
        now_s = self.clock()
        elapsed = now_s - self._last_optimize_ts
        if self._last_optimize_ts > 0 and elapsed < OPTIMIZE_COOLDOWN_SEC:
            wait = int(OPTIMIZE_COOLDOWN_SEC - elapsed) + 1
            return {
                "ok": False,
                "code": "OPTIMIZE_COOLDOWN",
                "cooldown_remaining_sec": wait,
                "message": f"操作过于频繁，请 {wait} 秒后再试",
            }
        before = parse_meminfo(self.meminfo_path.read_text(encoding="utf-8", errors="replace"))
        try:
            if hasattr(os, "sync"):
                os.sync()
        except Exception:
            pass
        self.drop_caches_path.write_text("1\n", encoding="utf-8")
        self._last_optimize_ts = now_s
        after = parse_meminfo(self.meminfo_path.read_text(encoding="utf-8", errors="replace"))
        reclaimed = max(0, after["ram_available_mb"] - before["ram_available_mb"])
        return {
            "ok": True,
            "reclaimed_mb": reclaimed,
            "before_available_mb": before["ram_available_mb"],
            "after_available_mb": after["ram_available_mb"],
            "before_used_pct": before["ram_pct"],
            "after_used_pct": after["ram_pct"],
            "buff_cache_after_mb": after["buff_cache_mb"],
            "message": f"已安全释放页缓存，可用内存 +{reclaimed} MB（当前可用 {after['ram_available_mb']} MB）",
        }

    def logs(self, source: str = "auth", only_alerts: bool = False, limit: int = 80) -> dict[str, Any]:
        limit = max(10, min(200, int(limit)))
        raw_lines: list[str] = []
        if source in self.log_files:
            raw_lines = tail_lines(self.log_files[source], limit * 2 if only_alerts else limit)
        elif source == "journal":
            try:
                proc = subprocess.run(
                    [
                        "journalctl",
                        "-u",
                        "solbot-*",
                        "-u",
                        "sol-*",
                        "-n",
                        str(limit * 2 if only_alerts else limit),
                        "--no-pager",
                        "-o",
                        "short-iso",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=4,
                    check=False,
                )
                if proc.returncode == 0:
                    raw_lines = proc.stdout.splitlines()
            except (OSError, subprocess.SubprocessError):
                raw_lines = []
        else:
            raise ValueError("unsupported log source")

        entries = []
        alert_count = 0
        for line in raw_lines:
            masked = mask_log_line(line)
            if not masked:
                continue
            level = classify_log_level(masked)
            if level in ("alert", "warn"):
                alert_count += 1
            if only_alerts and level == "info":
                continue
            entries.append({"level": level, "text": masked})
        entries = entries[-limit:]
        return {
            "ok": True,
            "source": source,
            "count": len(entries),
            "alert_count": alert_count,
            "entries": list(reversed(entries)),
        }

    def metrics(self) -> dict[str, Any]:
        mem = parse_meminfo(self.meminfo_path.read_text(encoding="utf-8", errors="replace"))
        cpu_pct = self._cpu_pct()
        try:
            load_parts = self.loadavg_path.read_text(encoding="utf-8").split()
            load_1m = float(load_parts[0])
            load_5m = float(load_parts[1])
        except (OSError, ValueError, IndexError):
            load_1m, load_5m = 0.0, 0.0
        try:
            uptime_sec = float(self.uptime_path.read_text(encoding="utf-8").split()[0])
        except (OSError, ValueError, IndexError):
            uptime_sec = 0.0
        disk = shutil.disk_usage("/")
        disk_total_gb = round(disk.total / (1024**3), 1)
        disk_used_gb = round(disk.used / (1024**3), 1)
        disk_free_gb = round(disk.free / (1024**3), 1)
        disk_pct = round(disk.used / disk.total * 100.0, 1) if disk.total else 0.0

        try:
            procs_total = sum(1 for entry in os.listdir("/proc") if entry.isdigit())
        except OSError:
            procs_total = 0

        services = self._systemd_services()
        services_active = sum(1 for s in services if s["active"])

        ssh_sessions = self._ssh_sessions()
        f2b = self._fail2ban_sshd()
        auth_lines = tail_lines(self.log_files["auth"], 400) if "auth" in self.log_files else []
        auth_summary = parse_auth_summary(auth_lines)
        clean_preview = self.clean_cache(dry_run=True)

        return {
            "ok": True,
            "hostname": socket.gethostname(),
            "cpu_pct": cpu_pct,
            "cpu_cores": os.cpu_count() or 1,
            "load_1m": round(load_1m, 2),
            "load_5m": round(load_5m, 2),
            "uptime_sec": int(uptime_sec),
            "uptime_text": format_uptime(uptime_sec),
            **mem,
            "disk_total_gb": disk_total_gb,
            "disk_used_gb": disk_used_gb,
            "disk_free_gb": disk_free_gb,
            "disk_pct": disk_pct,
            "processes_total": procs_total,
            "services": services,
            "services_active": services_active,
            "services_total": len(services),
            "cleanable_mb": clean_preview["total_freed_mb"],
            "cleanable_items": clean_preview["items"],
            "security": {
                "ssh_active_count": len(ssh_sessions),
                "ssh_sessions": ssh_sessions[:10],
                "ssh_failed_recent": auth_summary["ssh_failed_recent"],
                "last_failed_ip": auth_summary["last_failed_ip"],
                "last_accepted_ip": auth_summary["last_accepted_ip"],
                "fail2ban_currently_failed": f2b["currently_failed"],
                "fail2ban_total_failed": f2b["total_failed"],
                "fail2ban_currently_banned": f2b["currently_banned"],
                "fail2ban_banned_ips": f2b["banned_ips"],
            },
            "collected_at": int(self.clock() * 1000),
        }

    def handle(self, message: dict[str, Any]) -> dict[str, Any]:
        action = message.get("action")
        if action == "metrics":
            return self.metrics()
        if action == "logs":
            return self.logs(
                source=str(message.get("source") or "auth"),
                only_alerts=bool(message.get("only_alerts", False)),
                limit=int(message.get("limit") or 80),
            )
        if action == "optimize_memory":
            return self.optimize_memory()
        if action == "clean_cache":
            return self.clean_cache(dry_run=bool(message.get("dry_run", True)))
        raise ValueError("unsupported ops action")


def receive_line(sock: socket.socket) -> dict[str, Any]:
    data = b""
    while b"\n" not in data:
        block = sock.recv(4096)
        if not block:
            raise ValueError("incomplete request")
        data += block
        if len(data) > 16384:
            raise ValueError("oversized request")
    return json.loads(data.split(b"\n", 1)[0])


def serve(path: str, backend: OpsBackend, allowed_uid: int) -> None:
    if os.path.lexists(path):
        if not stat.S_ISSOCK(os.lstat(path).st_mode):
            raise ValueError("not a socket")
        os.unlink(path)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(path)
        os.chmod(path, 0o660)
        try:
            app_pw = pwd.getpwuid(allowed_uid)
            os.chown(path, 0, app_pw.pw_gid)
        except (KeyError, OSError):
            pass
        server.listen(16)
        while True:
            conn, _ = server.accept()
            with conn:
                conn.settimeout(10)
                try:
                    import struct
                    _, uid, _ = struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                    if uid not in (allowed_uid, 0):
                        raise PermissionError("peer denied")
                    req = receive_line(conn)
                    res = backend.handle(req)
                    conn.sendall(json.dumps(res, ensure_ascii=False).encode("utf-8") + b"\n")
                except Exception as exc:
                    try:
                        conn.sendall(json.dumps({"ok": False, "error": str(exc)[:120]}).encode("utf-8") + b"\n")
                    except OSError:
                        pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", default="/run/solbot-ops/ops.sock")
    parser.add_argument("--client-user", default="solbot-app")
    args = parser.parse_args()
    allowed_uid = pwd.getpwnam(args.client_user).pw_uid
    serve(args.socket, OpsBackend(), allowed_uid)


if __name__ == "__main__":
    main()
