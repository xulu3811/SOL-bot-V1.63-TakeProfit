"""Durable LIVE control with no key reader, private HTTP client or order transport."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import time

from solbot.risk import Guardian
from .models import AuthorizationRequest, ExecutionResult, LiveState, Mode, OrderRequest, SYMBOLS, identifier
from ._storage import verify_existing_store


class LiveControl:
    def __init__(self, path, *, symbol="SUIUSDT", account_id="SUI-live"):
        if symbol not in SYMBOLS:
            raise ValueError("unsupported live symbol")
        self.symbol, self.account_id = symbol, identifier(account_id, "account_id")
        self.path = Path(path).resolve()
        verify_existing_store(self.path, "LIVE", self.account_id, self.symbol)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("""CREATE TABLE IF NOT EXISTS execution_owner (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), kind TEXT NOT NULL,
                    account_id TEXT NOT NULL, symbol TEXT NOT NULL)""")
                db.execute("INSERT OR IGNORE INTO execution_owner VALUES (1,'LIVE',?,?)", (account_id, symbol))
                owner = db.execute("SELECT * FROM execution_owner").fetchone()
                if (owner["kind"], owner["account_id"], owner["symbol"]) != ("LIVE", account_id, symbol):
                    raise ValueError("execution database belongs to another channel, account or symbol")
                db.execute("""CREATE TABLE IF NOT EXISTS live_control (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), symbol TEXT NOT NULL,
                    account_id TEXT NOT NULL, state TEXT NOT NULL, reason TEXT NOT NULL,
                    revision INTEGER NOT NULL, updated_at INTEGER NOT NULL)""")
                db.execute("""CREATE TABLE IF NOT EXISTS live_control_events (
                    id INTEGER PRIMARY KEY, created_at INTEGER NOT NULL, actor TEXT NOT NULL,
                    request_id TEXT NOT NULL, state TEXT NOT NULL, reason TEXT NOT NULL)""")
                db.execute("INSERT OR IGNORE INTO live_control VALUES (1,?,?,'LIVE_OFF','first_start',1,?)",
                           (symbol, account_id, self._now()))
                row = db.execute("SELECT * FROM live_control WHERE singleton=1").fetchone()
                if row["symbol"] != symbol or row["account_id"] != account_id:
                    raise ValueError("live control file belongs to another account or symbol")
                if row["state"] in (LiveState.LIVE_ON, LiveState.LIVE_PENDING):
                    self._set(db, LiveState.PAUSED, "restart_requires_unimplemented_safety_revalidation", "server", "restart")
                db.commit()
            except BaseException:
                db.rollback()
                raise

    @staticmethod
    def _now():
        return time.time_ns() // 1_000_000

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        return db

    def _set(self, db, state, reason, actor, request_id):
        now = self._now()
        db.execute("UPDATE live_control SET state=?,reason=?,revision=revision+1,updated_at=? WHERE singleton=1",
                   (str(state), reason, now))
        db.execute("INSERT INTO live_control_events(created_at,actor,request_id,state,reason) VALUES (?,?,?,?,?)",
                   (now, actor, request_id, str(state), reason))
        db.execute("DELETE FROM live_control_events WHERE id < (SELECT MAX(id)-9999 FROM live_control_events)")

    def snapshot(self):
        with closing(self._connect()) as db:
            result = dict(db.execute("SELECT * FROM live_control WHERE singleton=1").fetchone())
        result.update(live_ready=False, guardian="NOT_IMPLEMENTED", private_adapter="NOT_IMPLEMENTED",
                      protective_orders="NOT_IMPLEMENTED", r1_live_allowed=False)
        return result

    def request_on(self, authorization: AuthorizationRequest) -> ExecutionResult:
        if not isinstance(authorization, AuthorizationRequest):
            raise ValueError("typed server authorization context required")
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT state FROM live_control WHERE singleton=1").fetchone()
            state = row["state"]
            if state == LiveState.HARD_LOCKED:
                code, reason = "DENY_HARD_LOCKED", "hard lock cannot be cleared by LIVE control"
            elif not authorization.authenticated:
                code, reason = "DENY_UNAUTHENTICATED", "server authentication is required"
            else:
                code, reason = "DENY_NOT_READY", "Guardian, private adapter and protective orders are not implemented"
                self._set(db, LiveState.LIVE_PENDING, "authorization_requested", authorization.actor, authorization.request_id)
                self._set(db, LiveState.LIVE_OFF, reason, authorization.actor, authorization.request_id)
                state = LiveState.LIVE_OFF
            db.commit()
        return ExecutionResult(False, code, (reason,), str(state))

    def request_off(self, *, actor="server", request_id="off") -> ExecutionResult:
        identifier(actor, "actor")
        identifier(request_id, "request_id")
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            state = db.execute("SELECT state FROM live_control WHERE singleton=1").fetchone()[0]
            if state != LiveState.HARD_LOCKED:
                state = LiveState.LIVE_OFF
                self._set(db, state, "user_requested_off", actor, request_id)
            db.commit()
        return ExecutionResult(True, "OFF_CONFIRMED" if state != LiveState.HARD_LOCKED else "HARD_LOCK_RETAINED", (), str(state))

    def force_lock(self, reason="maximum_drawdown_5_percent") -> ExecutionResult:
        if not isinstance(reason, str) or not reason or len(reason) > 300:
            raise ValueError("invalid hard-lock reason")
        with closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            self._set(db, LiveState.HARD_LOCKED, reason, "risk", "hard-lock")
            db.commit()
        return ExecutionResult(False, "HARD_LOCKED", (reason,), str(LiveState.HARD_LOCKED))


class LiveExecutor:
    """P2 request/error interface. Injected future factories are never called.

    Even forged LIVE_ON database state cannot bypass the explicit incomplete
    capability gate. This class has no executable real-order implementation.
    """
    def __init__(self, control: LiveControl, *, secret_loader=None, private_client_factory=None, guardian=None):
        self.control = control
        self._secret_loader = secret_loader
        self._private_client_factory = private_client_factory
        self.guardian = guardian if guardian is not None else Guardian()

    def execute_intent(self, intent):
        """P4 complete-intent boundary. No capability flag can open execution."""
        from solbot.risk.guardian import Guardian as P4Guardian
        from solbot.risk.permission_engine import TradeIntent
        if not isinstance(self.guardian,P4Guardian):
            return ExecutionResult(False,'DENY_GUARDIAN_UNAVAILABLE',('P4 persistent Guardian required',))
        if not isinstance(intent,TradeIntent):
            return ExecutionResult(False,'DENY_INCOMPLETE_INTENT',('complete typed intent required',))
        if intent.account_id!=self.control.account_id or intent.symbol!=self.control.symbol:
            return ExecutionResult(False,'DENY_ACCOUNT_SYMBOL',('live account and symbol must match',))
        try:
            result=self.guardian.authorize_live(intent)
            state=self.control.snapshot()['state']
        except (ValueError,sqlite3.Error,OSError):
            return ExecutionResult(False,'PAUSE_GUARDIAN_UNAVAILABLE',('risk state unavailable; no new entry',))
        return ExecutionResult(False,result.code,(result.code,),state)

    def execute(self, request: OrderRequest) -> ExecutionResult:
        if not isinstance(request, OrderRequest):
            raise ValueError("typed order request required")
        if request.mode != Mode.LIVE:
            return ExecutionResult(False, "DENY_CHANNEL_ISOLATION", ("shadow requests cannot enter LIVE",))
        if request.account_id != self.control.account_id or request.symbol != self.control.symbol:
            return ExecutionResult(False, "DENY_ACCOUNT_SYMBOL", ("live account and symbol must match",))
        state = self.control.snapshot()["state"]
        if state != LiveState.LIVE_ON:
            return ExecutionResult(False, "DENY_LIVE_STATE", ("LIVE is not authorized",), state)
        decision = self.guardian.authorize_live(request)
        return ExecutionResult(False, decision.code, decision.reasons, state)


def main():
    import argparse
    import os
    parser = argparse.ArgumentParser(description="Local OS-admin LIVE control; P2 can never enable trading")
    parser.add_argument("--database", required=True)
    parser.add_argument("action", choices=("status", "off", "lock", "on"))
    args = parser.parse_args()
    if args.action != "status" and (not hasattr(os, "geteuid") or os.geteuid() != 0):
        parser.error("mutating local control requires the OS administrator; no network control endpoint exists")
    control = LiveControl(args.database)
    if args.action == "on":
        result = control.request_on(AuthorizationRequest("os-admin", "local-on", True)).asdict()
    elif args.action == "off":
        result = control.request_off(actor="os-admin").asdict()
    elif args.action == "lock":
        result = control.force_lock().asdict()
    else:
        result = control.snapshot()
    print(json.dumps(result))


if __name__ == "__main__":
    main()
