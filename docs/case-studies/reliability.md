# Reliability

> The gap between a system that reports green and a system that is working.

This page is the long version of the table on the front page. Three indicators had been green for months. None of them was broken. None of them measured what its label promised.

I found all three in a single afternoon, and only because I set out to do something unrelated: start recording metrics so I would have numbers to write about.

---

## The starting point

The monitoring worked. Every five minutes a script checked 41 containers and 3 disks, restarted the non-critical ones that had fallen over, escalated the critical ones to Telegram, and moved on. It had been doing that reliably for months.

It also **threw everything away**. The alert went to Telegram and nothing was persisted. There had been a metrics database once, written by a component that was later dismantled; the table was still there, holding 265,365 rows, with nothing written to it for 70 days.

So the first task looked simple: give the monitor that already worked a memory.

```
grep -r "container_metrics" .
# (no results)
```

The writer did not exist. It had never been broken — it had been deleted along with the component that owned it, and nobody noticed because nothing depended on the data.

That is the first lesson of the day, and the cheapest one: **data nobody reads stops being produced and nobody finds out.**

---

## Finding 1 — a 100% success rate that measured exit codes

With metrics recording again, the historical remediation table became worth looking at. Two tables, actually — the schema had changed months earlier and the history was split. After consolidating them: **83 real remediation events** between March and May.

The distribution was not subtle.

```sql
SELECT container_name, COUNT(*) FROM restart_history
GROUP BY container_name ORDER BY 2 DESC LIMIT 5;
```

```
beszel              49
frigate              8
stirling-pdf         7
homelab-dashboard    5
audiobookshelf       2
```

**One container accounted for 59% of every automated intervention in the system.** Forty-nine restarts in seven weeks, and not a single alert had ever reached me — because the healer worked. Every restart was recorded as `success`, so nothing ever escalated.

### It was not flapping. It was six episodes.

```
2026-04-01  ██████ 6
2026-04-02  ███████████████████████████ 27
2026-04-04  ███ 3
2026-04-16  ███ 3
2026-04-18  ███████ 7
2026-05-05  ███ 3
```

Inside each episode the timestamps cluster tightly — 44 of 48 intervals under one hour, median 12 minutes:

```
05:50:26   05:55:22   06:02:38      ← three consecutive 5-minute cycles
06:55:23   07:00:09   07:05:12      ← three more, one hour later
07:55:25
```

That is not a container degrading. That is something knocking it over, and a healer restarting it on every cycle.

Resource exhaustion was the obvious hypothesis and the data killed it: mean memory 23 MB, peak 42 MB, mean CPU 0.05%. Nothing was starving.

### The evidence that mattered

The hourly aggregates for 18 April:

```
2026-04-18T05   12 samples   cpu 0.01   mem  17.7 MB
2026-04-18T06   12 samples   cpu 0.00   mem   0.0 MB   ← entire hour at zero
2026-04-18T07   12 samples   cpu 0.00   mem   0.0 MB   ← entire hour at zero
2026-04-18T08   12 samples   cpu 0.01   mem  23.8 MB
```

For two full hours, all twelve samples per hour show zero memory and zero CPU. The container was listed but not running.

During those same two hours the healer logged **four restarts, all four as `success`**.

The explanation is one line of code. The old healer recorded success when `docker restart` returned exit code 0 — which it does whenever the daemon accepts the command, regardless of whether the container then stays up. **The metric measured the exit code, not the outcome.**

A 100% success rate over 83 events is exactly the kind of number that ends up on a slide. It was measuring nothing.

### The fix: verify, then record

```python
def restart_container(name: str, reason: str = "") -> bool:
    """Restart and **verify**. Returns True only if the container is
    actually running afterwards."""
    try:
        subprocess.check_call(["docker", "restart", name], timeout=30)
    except Exception as e:
        metrics_db.record_restart(name, "failed", f"{reason} | command: {e}")
        return False

    sleep(VERIFY_DELAY_S)                      # 10 seconds
    running = container_is_running(name)       # docker inspect, not exit code

    if running:
        metrics_db.record_restart(name, "success", f"{reason} | verified")
    else:
        metrics_db.record_restart(
            name, "failed",
            f"{reason} | command returned 0 but not running after {VERIFY_DELAY_S}s")
    return running
```

`success` now means *observed running ten seconds later*. It is still not a guarantee — a container can come up and die a minute afterwards — but it is a claim about the system instead of a claim about a subprocess.

### The fix: a circuit breaker

Verification tells you a restart failed. It does not stop you from doing it forty-nine times.

```python
def breaker_decision(attempts: int, max_attempts: int = CB_MAX_ATTEMPTS):
    """Pure function, so it can be tested without Docker."""
    if attempts >= max_attempts:
        return False, (f"circuit breaker open: {attempts} attempts in the last "
                       f"{CB_WINDOW_HOURS}h (max {max_attempts})")
    return True, f"attempt {attempts + 1}/{max_attempts} in {CB_WINDOW_HOURS}h"
```

Three attempts in six hours and the monitor stops restarting, records the event as `circuit_breaker`, and sends a *different* alert — one that includes the container's last log lines.

Two decisions inside it are worth stating:

**The attempt count excludes `circuit_breaker` and `skipped` events.** Counting them would let the breaker feed itself and never close.

**If the database read fails, the count is zero.** When in doubt, attempt the restart. A storage problem must not silently disable remediation.

Calibrated against the real episode, the thresholds cut it here:

```
05:50:26  attempt 1/3   →  restart
05:55:22  attempt 2/3   →  restart
06:02:38  attempt 3/3   →  restart
06:55:23  BLOCKED       →  alert with container logs
```

I would have known at 06:55 on 18 April, instead of three months later while reading a database.

!!! warning "The root cause is still unknown"
    `beszel` has been stable since 5 May. This work detects and contains the failure class; it does not diagnose that particular container. If it recurs, the logs now arrive on my phone — which is what was missing.

---

## Finding 2 — `healthy` did not mean healthy

The new writer recorded `health='healthy'` for any container not explicitly reporting `unhealthy`. The old writer had recorded Docker's actual health field, which is **empty** when a container defines no `HEALTHCHECK`.

Empty and healthy are not the same thing. A container without a health check is not healthy; it is unchecked.

The consequence was a step change in the time series on the day I introduced it — an artefact of my own code, not of the system. Since `status` was stored verbatim, the correct value could be derived retroactively:

| Docker status | `health` |
| --- | --- |
| `Up 2 weeks (healthy)` | `healthy` |
| `Up 3 hours (unhealthy)` | `unhealthy` |
| `Up 5 seconds (health: starting)` | `starting` |
| `Up 10 hours` | `` — no health check defined |

**491 of 779 rows were wrong: 63%.**

The migration imports the parsing function from the monitor rather than reimplementing it, so the rule cannot drift between the two.

What makes this fix trustworthy is not that it ran — it is that the proportions line up across the boundary:

```
historical (Apr–May):     8,446 / 22,556 hours with a health check  =  37.4%
today, after the fix:       288 /    779 rows                       =  37.0%
```

Two independently produced datasets agreeing to within half a percentage point. Without that check I would have had a migration that ran cleanly and produced plausible garbage.

---

## Finding 3 — "Relays 5/5"

The daily report contained a line reading `Relays: 5/5`. There are nine relays. The five being counted were not a subset of the nine — three of them were reverse-proxy relays, unrelated to the hardware integrations.

Actual coverage, once audited:

| Relay | Coverage |
| --- | --- |
| Zigbee, two microcontroller bridges | Indirect — a separate monitor verifies the *effect* through the Home Assistant API |
| Two device relays | Direct |
| **Four remaining relays** | **None** |

The indirect coverage is arguably better than checking the relay: it verifies that the integration works, not that a process exists. But four relays had no coverage of any kind, and the line in the report read as reassurance.

The denominator was "things we happen to check", presented as "things that matter".

---

## The pipeline underneath

### Fail-safe by contract

Every function in the persistence layer catches its own exceptions and returns a neutral value. A monitor that runs every five minutes must not die because a disk write failed.

```python
def record_containers(rows: list[dict], timestamp: str | None = None) -> int:
    ...
    except Exception as e:
        _log(f"record_containers failed: {e}")
        return 0
```

This is a deliberate inversion of normal practice. In application code, swallowing exceptions hides bugs. In a monitor, an unhandled exception means **you stop finding out about everything else**, which is strictly worse than losing one sample.

### Retention that does not destroy the series

At 41 containers every five minutes, this produces roughly 11,000 rows a day — about 900 MB a year.

The naive fix, deleting old rows, would have erased the April–May history the same morning it was recovered. So the policy aggregates before it deletes:

- Full resolution kept for 30 days
- Older data collapsed into hourly averages per container and daily averages per disk
- Aggregates kept indefinitely

Two properties make it safe.

**The cutoff is truncated to the hour.** Aggregation and deletion use the identical boundary, so an hour is either wholly aggregated or wholly untouched. Nothing is ever deleted half-summarised.

```python
def _cutoff_hour(keep_days: int) -> str:
    """Truncating is what makes the policy safe: rollup and prune use exactly
    the same boundary, so an hour goes into the aggregate whole or not at all."""
    return datetime.fromtimestamp(time() - keep_days * 86400).strftime("%Y-%m-%dT%H")
```

**Deletion only touches what has already been aggregated.**

```sql
DELETE FROM container_metrics
WHERE substr(timestamp, 1, 13) < ?
  AND substr(timestamp, 1, 13) IN (SELECT hour FROM container_metrics_hourly)
```

If the rollup fails, the prune deletes nothing. The safety net is in the query, not in the caller.

First run:

```
265,365 detail rows  →  22,556 hourly rows
74.6 MB              →  2.9 MB
Apr–May series       →  intact, at hourly resolution
```

A 96% reduction with the shape of the series preserved.

---

## Heartbeats, and why discovery-based monitoring is blind

The scheduled-job monitor works by **discovery**: it lists the loaded jobs and reports on those. It answers "is this job loaded and did its last run succeed?"

It cannot answer "is a job missing?" If a job is never loaded, or gets unloaded, or fails to register after a reboot, it drops out of the count and the report goes from `21/21` to `20/20`. Still green. For a weekly job, that can go unnoticed for a month.

Heartbeats invert the question. Each job writes a record when it finishes; a manifest declares which jobs are expected and how stale each is allowed to be. The daily report compares.

This duplicates what a push monitor in Uptime Kuma already does, and rather than pick one, the heartbeat writes locally **and** pushes to Kuma. They fail differently:

| | Uptime Kuma push | Local heartbeat |
| --- | --- | --- |
| Immediate alerting | yes | no — next daily report |
| History and graphs | yes | no |
| External observer | **yes, separate host** | no |
| Works with network or DNS down | no | yes |
| Failure detail | truncated | full JSON |

Kuma runs on a different machine, which is the only way to detect that this machine is dead. The local file is the only thing that survives Kuma being dead. Neither subsumes the other.

Three design notes, each of which came from getting it wrong first:

**Kuma is addressed by IP, not hostname.** The heartbeat must not depend on internal DNS, which is itself one of the monitored services.

**A failed push does not invalidate the heartbeat.** The write returns the status of the local file only; the daily report depends on the file, not the network.

**The manifest self-heals.** The first version returned the config file as-is, so an existing manifest silently ignored options added later — which is exactly what happened to the `push` flag the day it was introduced. The push looked configured, both verification commands reported success, and nothing was being sent. It now merges defaults into missing keys and rewrites the file.

That last one is finding number four, found the same way as the others: a word missing from a line of output.

---

## Tests

86 assertions across three modules, none of which require Docker, a network, or the real database.

| Suite | Covers |
| --- | --- |
| `metrics_db` | writes, aggregation, retention, the safety net, never raising |
| `docker_monitor` | status parsing, stats parsing, health derivation, breaker decision |
| `heartbeat` | manifest merging, staleness, push failure, corrupt config |

The parsing functions were extracted from the monitor specifically so they could be tested against degenerate input — `--`, `n/a`, missing columns, `unhealthy` containing `healthy` as a substring, CPU above 100%.

The regression tests that matter most are the ones written after each mistake above. There is a test asserting that a manifest from an older version inherits new keys, and one asserting that a failed push still writes the local heartbeat.

---

## What this actually cost, and what is still open

The whole thing was one afternoon, and most of it was not writing code. It was reading data that had been sitting there for months.

Still open:

- The root cause of the container that consumed 59% of all remediations
- Four relays with no monitoring coverage
- Heartbeats cover one job; the rest of the scheduled work still relies on discovery
- No consistency check on the metrics series itself. All four findings were caught by eye

That last one is the uncomfortable one. **Every problem on this page was found by a human noticing something odd, not by a system designed to notice.** Building the check that would have caught them is the obvious next step, and it is not done.

---

## Three things I would tell myself in March

**Investigate before you compress.** The five-minute detail for April was aggregated in the morning; the analysis of the 49 restarts happened that afternoon. The hourly averages were enough to conclude, but the minute-by-minute sequence is gone for good.

**A green metric is not a correct metric.** All three indicators had been in the daily report for months. None was broken. Each measured something real. None measured what its label promised.

**Discovery-based monitoring cannot see absences.** It is convenient because there is no list to maintain, and that convenience is exactly what turns a component disappearing into silence.
