# SOL-bot P1 public market data service

The service is restricted to SOLUSDT public Spot market data. No account credentials, order client, LIVE switch or strategy execution is provided.

## Runtime and data

- Code `/opt/sol-bot/backend`, root-owned release symlink.
- Runtime `/opt/sol-bot/venv/bin/python` (CPython 3.14).
- Database `/var/lib/sol-bot/database/market.sqlite`, owned by `solbot`.
- API `http://127.0.0.1:8000`, single process and database writer.
- Service `solbot-market.service`. Existing `sol-bot-backup.timer` and `sol-bot-health.timer` remain separate.
- Dependency lock contains exact versions and SHA256 wheel hashes for CPython 3.14/Linux x86_64. Re-resolve on other platforms rather than disabling hash checks.

## API

`GET /health` returns 200 only when ready; 503 explains unavailable database, incomplete history, gaps or stale streams. `GET /api/v1/market/status` returns detailed diagnostics. `klines` accepts interval, symbol=SOLUSDT, limit1..1000 and inclusive UTC millisecond start/end. Follow `next_start` for pagination. `latest` returns the latest closed candle. `gaps` accepts bounded limit/offset and optional interval.

Five native streams (1m,5m,15m,1h,4h) are independent. Only explicitly closed WS candles enter the table. REST candles close only when their close timestamp precedes the synchronized exchange clock. Prices and volumes remain decimal strings. Initialization covers the previous30 complete UTC calendar days and current day's completed candles. Gaps remain unavailable until repaired from official REST.

Official specifications used:
- https://github.com/binance/binance-spot-api-docs/blob/master/rest-api.md
- https://github.com/binance/binance-spot-api-docs/blob/master/web-socket-streams.md

## Run tests

On the server as solbot with `PYTHONPATH=/opt/sol-bot/backend/src`:

```sh
/opt/sol-bot/venv/bin/python -m unittest discover -s /opt/sol-bot/tests/unit -v
/opt/sol-bot/venv/bin/python -m unittest discover -s /opt/sol-bot/tests/integration -v
/opt/sol-bot/venv/bin/python /opt/sol-bot/tests/integration/resource_probe.py
```

Unit/integration discovery uses isolated temporary databases and fake network inputs. Live evidence is recorded separately. Load traffic is synthetic and stays local. A finite observation window cannot establish long-term availability.

## Deployment and rollback

`scripts/package_p1.py` includes only backend/tests/deploy/docs and excludes caches and database files. `scripts/p1-deploy.sh` extracts a versioned release, refuses an unmanaged backend, preserves previous symlink destinations and service unit, runs offline tests, and then enables/restarts only `solbot-market.service`.

Rollback by stopping that service, restoring each previous backend/tests/deploy/docs symlink recorded under the failed release, restoring `previous-service.unit` if present, running `systemctl daemon-reload`, and starting the last validated version. For the first release, stop/disable the new service and retain source and database for diagnosis. Never delete a market database to fix startup. Schema version compatibility must be checked before a downgrade; restore a consistent backup into an isolated path first if required.

## Backups, retention and recovery

The inherited daily backup at local03:15 with up to10min jitter discovers `.sqlite` files, uses SQLite's online backup API, sets mode0640, and retains seven days. This covers `market.sqlite`. Verify actual successful artifacts after database creation. Recovery is performed into an isolated file and validated using `PRAGMA integrity_check` plus counts/ranges before switching a stopped service to it. Reconcile any newer missing candles from official REST after recovery. A same-host copy does not protect against host/disk loss; off-host recovery remains deferred.

P1 retains captured candles to avoid deleting research data without an agreed retention policy. Monitor growth and determine a retention horizon before multi-month operation. Connection/health metadata use bounded retention in storage. Service journal rate limiting is configured; host journal retention follows existing system policy and must be reviewed if disk growth increases. SQLite WAL autocheckpoint limits normal growth, but long read transactions can prevent truncation; API queries are short and bounded.

## Security tracking

User reports management password changed and cloud console login verified. Prior plaintext exposure remains HIGH pending independent old-credential invalidation verification. P1 only binds loopback; SSH, UFW and existing recovery services are outside P1 modifications. Changes to frozen drawdown rules are outside this service.
