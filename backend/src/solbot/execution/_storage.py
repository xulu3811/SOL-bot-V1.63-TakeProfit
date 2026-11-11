"""Read-only ownership preflight before any execution-store initialization."""
from contextlib import closing
import sqlite3


def verify_existing_store(path, kind, account_id, symbol):
    if not path.exists():
        return
    allowed = {
        "LIVE": {"execution_owner", "live_control", "live_control_events"},
        "SHADOW": {"execution_owner", "shadow_account", "shadow_orders", "shadow_positions"},
        "SHADOW_P3": {"execution_owner", "shadow_account", "shadow_orders", "shadow_positions",
                      "shadow_protection", "shadow_fills", "shadow_math_audit", "shadow_p3_metadata"},
    }[kind]
    # mode=ro cannot create a missing database or change its schema/journal mode.
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        if not tables:
            return
        if not tables <= allowed or "execution_owner" not in tables:
            raise ValueError("refusing to initialize execution in a foreign database")
        owner = db.execute("SELECT kind,account_id,symbol FROM execution_owner WHERE singleton=1").fetchone()
        if owner != (kind, account_id, symbol):
            raise ValueError("execution database belongs to another channel, account or symbol")
