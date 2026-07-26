# Operational code

A sanitized snapshot of the code discussed in [Reliability](../docs/case-studies/reliability.md). It is the real thing, not a demo — the same modules that run every five minutes on the host — with absolute paths replaced by environment variables and defaults that resolve inside this repository.

**Standard library only.** No dependencies, no virtualenv. These run on a scheduler under `launchd`, and every dependency is one more thing that can be missing at 3 a.m.

## Files

| File | What it does |
| --- | --- |
| `docker_monitor.py` | The monitor. Checks containers and disks every five minutes, records metrics, restarts non-critical containers and **verifies the restart**, escalates critical ones, opens a circuit breaker on repeated failures. |
| `metrics_db.py` | Persistence. Writes, aggregation, retention with downsampling, and the safety net that prevents deleting un-aggregated data. |
| `heartbeat.py` | Liveness for scheduled jobs. Writes a local record and optionally pushes to an external uptime monitor. |
| `homelab_secrets.py` | Credential loading from environment or files outside version control. |
| `test_docker_monitor.py` | Tests for the parsing and circuit-breaker logic. |

## Running it

Every module self-tests against temporary databases and touches nothing real:

```bash
python3 metrics_db.py --selftest          # 22 assertions
python3 test_docker_monitor.py            # 24 assertions
python3 heartbeat.py --selftest           # 21 assertions
```

The monitor has a dry run that records metrics but neither restarts anything nor sends notifications:

```bash
python3 docker_monitor.py --dry-run
```

## Configuration

Defaults resolve relative to this directory, so the self-tests work on any machine with no setup.

| Variable | Default | Purpose |
| --- | --- | --- |
| `HOMELAB_DB` | `./data/homelab.db` | Metrics database |
| `HOMELAB_STATE` | `./data/docker_monitor_state.json` | Last-known state, for edge-triggered alerts |
| `HOMELAB_HEARTBEATS` | `./data/heartbeats/` | Heartbeat records and manifest |
| `HOMELAB_SECRETS` | `./.secrets/telegram.env` | Credentials file |
| `HOMELAB_MAINT_FILE` | `./data/.maintenance_until` | Maintenance window; suppresses notifications |
| `DOCKER_BIN` | `docker` | Docker binary |
| `DISK_DATA`, `DISK_BACKUP` | `/data`, `/backup` | Mount points to monitor |
| `KUMA_BASE_URL` | `http://localhost:3001` | External uptime monitor |

Credentials are read from the environment first, then from `HOMELAB_SECRETS` and any other `*.env` beside it. Nothing is ever read from source.

## Three things worth reading for

**Nothing raises.** Every function in `metrics_db.py` and `heartbeat.py` catches its own exceptions and returns a neutral value. In application code that hides bugs; in a monitor, an unhandled exception means you stop hearing about everything else. `_selftest` asserts this explicitly by pointing the database at an unwritable path.

**`success` means verified.** `restart_container()` waits, then asks `docker inspect` whether the container is actually running. It records `failed` when the command returned zero and the container did not come back. The [Reliability](../docs/case-studies/reliability.md) page explains why that distinction cost seven weeks.

**Deletion is guarded in SQL, not in the caller.** `prune()` will only delete an hour of raw data that already exists in the hourly aggregate. If the rollup fails, the prune is a no-op. The safety net is in the query so it cannot be bypassed by calling things in the wrong order.

## A caveat

This is a snapshot, not a mirror. The copies running on the host are the source of truth and will drift from these. Where that matters, the [documentation](../docs/) describes the behaviour rather than the line numbers.
