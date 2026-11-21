# Reliability

> The distance between a system that reports green and a system that works.

This page is the long version of the table on the home page. Three indicators had been green for months. None was broken. None measured what its label promised.

I found all three in a single afternoon, and only because I was doing something else: starting to record metrics so I'd have numbers to tell.

---

## What existed before starting

It's worth understanding what the system did, because everything that follows is a nuance on top of this base.

A Python script, with no external dependencies, launched by the macOS scheduler (`launchd`) **every five minutes**. On each run:

1. It lists the 41 containers with `docker ps -a` and keeps name, status and whether it is running.
2. For each one it decides which category it falls into:
   - **Critical** (Home Assistant, the databases, the reverse proxy, the password manager): if it goes down, **it alerts but touches nothing**. Restarting a database blindly can be worse than the outage.
   - **Never touch**: a container switched off on purpose, which is ignored.
   - **Everything else**: if it went down, **it restarts it on its own**.
3. It checks the usage percentage of the three disks against their thresholds.
4. It sends to Telegram whatever needs sending.

A design detail that matters for everything that follows: **it only notifies on state change**. It stores in a JSON file whether each container was fine on the previous run, and only speaks when it goes from fine to bad or from bad to fine. Without that, a container that is down would generate 288 messages a day.

This had been working without failing for months. And **it threw everything away**: the alert went to Telegram, the message was read, and nothing was recorded.

### The first attempt to fix it

There was a SQLite database with a table called `container_metrics`. It had 265,365 rows. The most recent was seventy days old.

My first hypothesis was the obvious one: something broke and nobody noticed. So I went looking for the code that wrote to that table.

```
grep -r "container_metrics" .
# (no results)
```

Zero results in the whole repository. It wasn't that the writer was failing: **it didn't exist**. Digging a bit further, the explanation appeared: the table was fed by a component that was dismantled months earlier. When the component was deleted, it took the writer with it. The database stayed there, orphaned, and nobody missed it because nothing read it.

So the real task wasn't repairing anything. It was **giving memory to the monitor that already worked**, which every five minutes had all that data in hand and was throwing it away.

That is the first lesson of the day, and the cheapest one: **data nobody reads stops being produced and nobody finds out.** There is no possible alert for the disappearance of something nothing depends on.

---

## Finding 1: a 100% success rate that measured exit codes

### How I came to look there

Once the monitor started writing metrics, I had an obvious question left: **how many times has this system intervened, and with what result?** It is the number that sums up whether automatic recovery adds anything or is decoration.

The answer should be in a table called `restart_history`. And that's where the second problem started.

### The history was split in two

There were **two** tables, not one:

| Table | Period | Rows | Schema |
| --- | --- | --- | --- |
| `restart_history_v0` | Mar – 20 Apr 2026 | 79 | `timestamp` as ISO text, `success` as integer 0/1 |
| `restart_history` | 23 Apr – May 2026 | 7 | `timestamp` as epoch, `result` as text |

Someone (me) had changed the schema in April: going from a `success` boolean to a text `result` field that allowed more states than "went well / went badly". The migration created the new table and left the old one where it was. Reasonable at the time, and the reason why three months later the data couldn't answer a simple question.

Of the 7 rows in the new table, on top of that, **three were test garbage**: a container literally called `contenedor-que-no-existe` ("container-that-doesn't-exist"), and two from when I was testing the healer.

### Consolidating them

I wrote a migration instead of running an `INSERT ... SELECT` by hand, for three reasons that seem excessive at the time and later don't:

**Dry run by default.** The script writes nothing unless you pass `--apply`. It prints the plan: how many rows it will insert, from what date range, how many test rows it has detected.

**Backup with the SQLite API, not with `cp`.** The database is live: the monitor writes to it every five minutes. Copying the raw file while there is an open transaction and an unconsolidated WAL gives an inconsistent copy. `sqlite3.backup()` respects the lock and produces a consistent copy.

**Idempotency through a `migrations` table.** The script records itself when it finishes. If you run it twice, the second does nothing and tells you. Without that, a slip of the finger duplicates 79 historical events and corrupts every percentage that comes after.

The conversion itself was trivial: ISO `timestamp` to epoch, and `success = 1` to `result = 'success'`. What wasn't trivial was checking there was no overlap: if the two tables overlapped in time, there would be duplicate events. They didn't: the old one ended on 20 April, the new one started on the 23rd.

Result: **83 real events** from March to May, with the three test rows removed and `restart_history_v0` kept intact as a backup.

### The distribution wasn't subtle

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

**A single container accounted for 59% of all the system's automatic interventions.**

And here is what makes it more than a curiosity: forty-nine restarts in seven weeks and **I never got an alert**. The "only notify on state change" logic was doing its job: the container went down, the healer brought it up, the state went back to fine, and the message that reached Telegram was the recovery one. An occasional message saying something fixed itself doesn't trigger any mental alarm.

The system was masking a chronic problem effectively enough that it never escalated.

### First hypothesis: flapping. Ruled out.

My initial reading was "unstable container that goes down often". The data says something else. Grouping the 49 restarts by day:

```
2026-04-01  ██████ 6
2026-04-02  ███████████████████████████ 27
2026-04-04  ███ 3
2026-04-16  ███ 3
2026-04-18  ███████ 7
2026-05-05  ███ 3
```

It isn't a constant trickle. They are **six discrete episodes** separated by weeks of total stability. Between 4 and 16 April absolutely nothing happened.

And within each episode, the time structure is very characteristic. The intervals between consecutive restarts: 44 of 48 under an hour, with a median of **12 minutes**. Zooming into the 18 April episode:

```
05:50:26   05:55:22   06:02:38      ← three consecutive 5-minute cycles
06:55:23   07:00:09   07:05:12      ← another three, an hour later
07:55:25
```

Bursts of three restarts in consecutive monitor cycles, separated by about fifty minutes of calm, and round again. That isn't a container degrading little by little. It is something knocking it down at once, a healer bringing it up, and the cycle repeating.

### Second hypothesis: resources. Also ruled out.

The natural suspicion with a container that goes down in a loop is memory: a leak that takes it to the limit, the kernel kills it, it starts again, and round again. It's the most common pattern there is.

The hourly aggregates rule it out beyond any doubt:

- Average memory: **23 MB**. Absolute peak in five weeks: **42 MB**.
- Average CPU: **0.05%**. Peak: 0.56%.

On a machine with 64 GB of unified memory. There was no leak, no pressure, nothing resembling resource exhaustion. Whatever was knocking that container down, it wasn't its own consumption.

### The evidence that did matter

Looking at the hourly metrics for 18 April around the restarts, this appeared:

```
2026-04-18T05   12 samples   cpu 0.01   mem  17.7 MB
2026-04-18T06   12 samples   cpu 0.00   mem   0.0 MB   ← whole hour at zero
2026-04-18T07   12 samples   cpu 0.00   mem   0.0 MB   ← whole hour at zero
2026-04-18T08   12 samples   cpu 0.01   mem  23.8 MB
```

Twelve samples per hour is exactly what a monitor running every five minutes produces. So during hours 06 and 07, in **all twenty-four samples**, the container used zero memory and zero CPU.

A running container, however idle, uses something. Zero means it wasn't running. It showed up in the listing (that's why there are samples) but it was stopped.

And in those same two hours, the healer recorded **four restarts. All four as `success`.**

### Why the record lied

The explanation fits in one line of code. The old healer did this:

```python
subprocess.check_call(["docker", "restart", name])
registrar(name, exito=True)   # ← if we got here, the command didn't raise
```

`docker restart` returns exit code 0 as soon as the daemon **accepts** the order. It doesn't wait for the container to start, doesn't check it stays alive, knows nothing about what happens next. If the container starts and dies two seconds later, the command returned 0 long ago.

So the metric didn't measure "the problem was solved". It measured **"Docker accepted my order"**. Which is true practically always.

A 100% success rate over 83 events is exactly the kind of number that ends up on a slide and nobody questions because it's round. It wasn't measuring anything.

### Fix 1: verify before recording

```python
def restart_container(name: str, reason: str = "") -> bool:
    """Restart and **verify**. Returns True only if the container
    is really running afterwards."""
    try:
        subprocess.check_call(["docker", "restart", name], timeout=30)
    except Exception as e:
        metrics_db.record_restart(name, "failed", f"{reason} | command: {e}")
        return False

    sleep(VERIFY_DELAY_S)                      # 10 seconds
    running = container_is_running(name)       # docker inspect, not the exit code

    if running:
        metrics_db.record_restart(name, "success", f"{reason} | verified")
    else:
        metrics_db.record_restart(
            name, "failed",
            f"{reason} | returned 0 but not running after {VERIFY_DELAY_S}s")
    return running
```

The real check is `docker inspect -f {{.State.Running}}`, which asks the daemon for the container's current state instead of for the result of a past order.

**Why ten seconds.** It's a compromise. Too short and you catch the container mid-start, marking it as failed when it was going to come up fine. Too long and you stretch every monitor cycle for nothing. Ten seconds covers the start-up of everything running on this machine, and it's only paid when a restart is involved: in a normal cycle, with no outages, the cost is zero.

**What it still doesn't guarantee.** A container can be up at ten seconds and die a minute later. This verification doesn't catch it; the next cycle will, five minutes later. The difference is that now it's a statement about the state of the system instead of a statement about a subprocess, and that can be counted.

The Telegram message changed too, because the previous one inherited the same lie: where it said "✅ Restarted automatically" it now says **"✅ Restarted and verified"** or **"❌ Restart had no effect: still down"**.

### Fix 2: a circuit breaker

Verification tells you a restart didn't work. It doesn't stop you trying forty-nine times.

A container in a crash loop isn't fixed by restarting it; the restart is precisely what keeps the problem from becoming visible. At some point the system needs to **stop trying and call a human**.

```python
def breaker_decision(attempts: int, max_attempts: int = CB_MAX_ATTEMPTS):
    """Pure function, so it can be tested without Docker."""
    if attempts >= max_attempts:
        return False, (f"circuit breaker open: {attempts} attempts in the last "
                       f"{CB_WINDOW_HOURS} h (max {max_attempts})")
    return True, f"attempt {attempts + 1}/{max_attempts} in {CB_WINDOW_HOURS} h"
```

The decision being a pure function (it receives a number, returns a boolean and a reason) isn't cosmetic: it allows testing the circuit breaker's behaviour without Docker, without a database and without waiting six hours.

When it opens, the monitor stops restarting, records the event as `circuit_breaker`, and sends an alert **different from the usual one**, which includes the container's last log lines. That detail matters: when you finally get told something is looping, the first thing you'll want is precisely the log, and if it isn't in the message you have to go to the computer to find it.

**The thresholds: 3 attempts in 6 hours.** They aren't arbitrary; they come from the real episode. With those values, 18 April would have gone like this:

```
05:50:26  attempt 1/3   →  restarts
05:55:22  attempt 2/3   →  restarts
06:02:38  attempt 3/3   →  restarts
06:55:23  BLOCKED       →  🔴 alert with the container's logs
```

Three legitimate attempts (because sometimes a restart does solve it) and on the fourth it stops. I would have found out at 06:55 on 18 April, instead of three months later reading a database.

**Two internal decisions that look like details and aren't:**

*The count excludes the `circuit_breaker` and `skipped` events themselves.* It only counts real restart attempts. If it also counted its own decisions not to act, every cycle would add a row to the count, the number would grow by itself, and the circuit breaker would never close. It would feed itself.

*If reading the database fails, the count returns zero.* That is: faced with a storage problem, the system **does try** the restart. The alternative (failing towards blocking) would mean a full disk silently disables all automatic recovery, which is a much worse failure mode than restarting too much.

**How it closes.** Only with the passage of time: when the attempts leave the six-hour window, it allows again. There's no manual reset, which is an acknowledged gap: forcing it today means deleting rows from the history, which is ugly. A reset command over Telegram would be the right thing.

!!! warning "The root cause is still unknown"
    `beszel` has been stable since 5 May. All of this detects and contains the **class** of failure; it doesn't diagnose that specific container. What we know: it wasn't memory, it wasn't CPU, and it came in episodes separated by weeks. What we don't know: what was knocking it down. If it happens again, the logs reach the phone at the moment it happens, which is exactly what was missing to be able to investigate it.

---

## Finding 2: `healthy` didn't mean healthy

### How it showed up

I didn't find this one auditing anything. I found it skimming a sample of the hourly aggregate, a while after generating it, and seeing a column full of zeros where I expected variety.

Docker has the concept of a *health check*: a command the container runs periodically on itself to say whether it is healthy. It is **optional**. A container may define it or not, and when it doesn't, the `health` field Docker returns comes back **empty**.

When instrumenting the metrics I wrote this:

```python
"health": "healthy" if c["healthy"] else "unhealthy"
```

Where `c["healthy"]` was simply "the status text doesn't contain the word unhealthy". That is: everything not explicitly broken was marked healthy, **including containers that have no way of knowing**.

Empty and healthy aren't the same. A container with no health check isn't healthy: it's **unchecked**. Confusing them turns a health metric into a "hasn't blown up yet" metric.

### Why it mattered more than it seems

The old writer, from the dismantled component, stored Docker's real field. Mine stored my interpretation. The two halves of the time series measured different things under the same label.

In practice that means any health chart over time would show a **sharp step on 26 July**, the day I introduced my version. Someone looking at that chart six months from now would conclude something changed in the homelab that day. Nothing changed in the homelab: my code changed.

### The fix, and why it could be retroactive

Luckily the `status` field (the full text Docker returns, like `Up 2 weeks (healthy)`) was stored intact. The correct value could be **derived** from it for the rows already written:

| Docker status | Derived `health` |
| --- | --- |
| `Up 2 weeks (healthy)` | `healthy` |
| `Up 3 hours (unhealthy)` | `unhealthy` |
| `Up 5 seconds (health: starting)` | `starting` |
| `Up 10 hours` | `` (no health check defined) |
| `Exited (1) 3 minutes ago` | `` (no health check defined) |

A detail that almost created another bug: `unhealthy` **contains** `healthy` as a substring. If you check first whether `healthy` appears, every sick container is classified as healthy. The order of the checks is the only thing preventing that error, and that's why there's an explicit test for it.

**491 of 779 rows were wrong: 63%.**

The migration imports the parsing function directly from the monitor instead of reimplementing the rule, so they can't diverge. If tomorrow I change how `health` is derived, the correction code changes with it.

### The check that makes the fix trustworthy

A migration running without errors means nothing. The question to ask is whether the result is *plausible*, and for that you need to compare it against something you didn't just compute yourself.

In this case, against the history:

```
history (Apr-May):        8,446 / 22,556 hours with health check  =  37.4%
today, after the fix:       288 /    779 rows                     =  37.0%
```

Two datasets generated months apart, by different writers, agreeing within half a percentage point. That is what confirms the derivation is correct: the proportion of containers with a health check defined hasn't changed, and now both halves measure it the same way.

Without that check I'd have a clean migration producing plausible garbage, which is the default result of any data migration.

---

## Finding 3: "Relays 5/5"

### The context you need

Home Assistant runs in a container and, because of how container networking works on macOS, **it can't reach the local network**. All the physical devices in the house are on the other side of that boundary.

The solution is **nine** `socat` **relays** running natively on the host: each one listens on a port and forwards to a specific device. The container talks to the host and the host talks to the device. If a relay dies, Home Assistant loses that integration and shows the device as unavailable, with no hint as to why.

Nine independent points of failure, invisible from inside the container. Worth watching.

### What the report said

The 09:00 daily report included a line: `📡 Relays: ✅ 5/5`.

Five of five. Green. But there are nine.

### What I found when I looked

The five it counted **weren't a subset of the nine**. Three of them were reverse proxy relays (networking things completely unrelated to home automation) that were in the same state file for historical reasons. Only two of the nine device relays were there.

Real coverage, once thoroughly audited:

| Relay | Coverage | How |
| --- | --- | --- |
| Zigbee, and two microcontroller bridges | **Indirect** | Another monitor checks every 15 minutes, through the Home Assistant API, that those integrations respond |
| Two device relays | **Direct** | Check of the relay itself |
| The remaining four | **None** | — |

There's a nuance in the system's favour: indirect coverage is **better** than checking the relay. Verifying that the relay exists proves there's a process listening; verifying through the Home Assistant API proves the integration really works end to end. It is the right check.

But four relays had no coverage of any kind. If one has been down for weeks, there's no way of knowing.

### The underlying problem

The denominator was **"the things we happen to look at"**, presented as **"the things that matter"**. It's the same thing that happened with the recovery success rate, in another format: a ratio whose bottom part defined itself, by accumulation, and whose label describes something else.

And it's an especially treacherous pattern because a `5/5` ratio reads as completeness. Nobody looks at a `5/5` and asks "five of what".

---

## The pipeline underneath

So much for the findings. This part is the system built so that next time the data is there.

### Fail-safe by contract

Every function in the persistence layer catches its own exceptions and returns a neutral value:

```python
def record_containers(rows: list[dict], timestamp: str | None = None) -> int:
    ...
    except Exception as e:
        _log(f"record_containers failed: {e}")
        return 0
```

It's a deliberate inversion of good practice, and it deserves defending because it looks like a mistake.

In application code, swallowing exceptions hides bugs and is one of the worst things you can do. In a monitor the equation flips: an uncaught exception kills the process, `launchd` records the failure, and **you stop hearing about everything else** until the next cycle or until someone looks. Losing one metrics sample is trivial damage. Losing all monitoring because a disk write failed is major damage.

The contract is verified in the selftest itself: there's an assertion that points the database at a path where nothing can be written and checks that the function returns 0 instead of raising.

### The volume problem

41 containers × 12 samples/hour × 24 hours ≈ **11,800 rows a day**. About 4.3 million a year. At the ~230 bytes per row this table takes, that's **almost 900 MB a year** of container metrics alone.

Perfectly affordable on disk. Not in usability: queries for any chart get slow, and nightly backups carry data at a granularity absurd for its age. Nobody cares about minute-by-minute CPU usage from eight months ago.

### Why the obvious solution was dangerous

The natural thing is a purge: delete everything older than N days. With a 90-day retention, run on 26 July, the cut falls on 27 April.

And the history rescued that same morning ran **from 17 April to 17 May**. The purge would have taken the first ten days at once, and the whole block in September. It would have destroyed, that very day, the data that had just enabled every finding on this page.

### Two-tier retention

So the policy **aggregates before deleting**:

1. Five-minute detail is kept **30 days**.
2. Anything older is compressed into **hourly averages per container** (number of samples, average and max CPU, average and max memory, health ratio) and daily averages per disk.
3. Aggregates are kept **forever**.
4. The file is compacted with `VACUUM`.

You lose the fine resolution of the past and keep the shape of the series, which is what gets used beyond a certain age.

**First safety property: the cutoff is truncated to the hour.**

```python
def _cutoff_hour(keep_days: int) -> str:
    """Truncating is what makes the policy safe: rollup and prune use
    exactly the same cutoff, so an hour goes in whole or not at all."""
    return datetime.fromtimestamp(time() - keep_days * 86400).strftime("%Y-%m-%dT%H")
```

Without truncation, the cutoff would fall mid-hour: the aggregation would summarise half an hour, the purge would delete the detail, and that half hour would be miscounted forever. With the cutoff truncated and shared by both operations, an hour goes into the aggregate whole or not at all.

**Second property: deletion only touches what's already aggregated.**

```sql
DELETE FROM container_metrics
WHERE substr(timestamp, 1, 13) < ?
  AND substr(timestamp, 1, 13) IN (SELECT hour FROM container_metrics_hourly)
```

The second condition is the safety net. If aggregation fails for whatever reason, those hours don't exist in the aggregate table and the purge deletes nothing. **The system fails towards keeping data.**

And it's in the SQL on purpose, not in the calling code. A check in the caller can be skipped by calling the functions in the wrong order; a condition inside the query can't.

**Result of the first real pass:**

```
265,365 detail rows  →  22,556 hourly rows
74.6 MB              →  2.9 MB
April-May series     →  intact, at hourly resolution
```

A 96% reduction while keeping the full shape of the series. The compression is so large because twelve samples per hour per container collapse into a single row.

---

## Heartbeats, and why discovery-based monitoring is blind

### The hole

Retention runs once a week, on Sundays at 06:00. And here a new problem appears: **how do I know it ran?**

The system already watched scheduled tasks, but it did so **by discovery**: it asks the scheduler which tasks it has loaded and reports on them. It answers "is this task loaded, and did its last run go well?" fine.

It can't answer "**is any task missing?**". If a `plist` never gets loaded, or someone unloads it, or registration fails after a reboot, the task disappears from the count. The report goes from `21/21` to `20/20` and **stays green**, because the percentage is the same.

For a weekly task it's the worst possible scenario: it could go a month without running and nothing would scream.

### The inversion

A heartbeat asks the question the other way round. Instead of "is the task loaded?", it asks "**was the work done, and how long ago?**".

Each task writes a JSON file when it finishes, with its status and a detail. A manifest declares which tasks are expected and what maximum age is tolerated for each. The daily report compares and detects three different things:

- **Missing**: the task has never sent a heartbeat
- **Stale**: the last heartbeat is older than the tolerated maximum
- **Error**: the task ended with a non-zero code

It also covers the case discovery doesn't see either: a loaded task that fails silently.

### Not choosing between local and external

This partly duplicates what a *push* monitor in Uptime Kuma already does, which is the canonical tool for watching scheduled tasks. Instead of picking one, the heartbeat does both in the same call, because **they fail differently**:

| | Push to Uptime Kuma | Local heartbeat |
| --- | --- | --- |
| Immediate alert | yes, with notification channels | no: waits for the 09:00 report |
| History and charts | yes | no |
| External observer | **yes, another machine** | no |
| Works without network or DNS | no | yes |
| Failure detail | truncated to 200 characters | full JSON |
| Registering a task | create a monitor in the UI | one line in a file |

The split has a concrete reason. Kuma runs on another machine, and that's right: an external observer can detect that the main server is dead, something no local monitor can ever do. But it raises the reverse question: **who watches Kuma?** If that machine goes down, you stop getting alerts and silence looks like normality. The local heartbeat covers part of that gap: the main server's daily report would keep arriving even if Kuma were dead.

### Three decisions, all from having done it wrong before

**Kuma is addressed by IP, not by name.** The heartbeat can't depend on internal DNS, because internal DNS is itself one of the monitored services. If it goes down, the alert that it went down has to be able to get out anyway.

**A failed push doesn't invalidate the heartbeat.** The function returns only the status of the local write. If Kuma is down it's logged to `stderr` and that's it: the daily report relies on the file, not the network. There's a test that verifies exactly this, pointing at a closed port and checking that the local file is written anyway.

**The manifest fills itself in.** And this is the good one.

The first version of the loader returned the configuration file as it was. When I later added the option to push to Kuma, an **already created** manifest didn't have that key, and so silently ignored it. The push was never sent.

What makes it illustrative is that **both checks I ran came back green**: the push test command worked, because it skips the manifest and goes direct; and the heartbeat write command said "recorded", because the local file was indeed written. All correct, and nothing reached Kuma.

I caught it because one line of output was missing a three-word suffix.

Now the loader merges the defaults into the missing keys and rewrites the file. And the status messages say explicitly what they did **not** do, instead of leaving it out. It's the fourth finding of the day, found the same way as the other three.

---

## Tests

86 assertions in three modules. None needs Docker, network, or the real database: they all run against temporary databases destroyed when they finish.

| Suite | Covers |
| --- | --- |
| `metrics_db` | writes, aggregation, retention, the purge's safety net, never raising |
| `docker_monitor` | status parsing, stats parsing, health derivation, circuit breaker decision |
| `heartbeat` | manifest merging, stale heartbeats, push failure, corrupt configuration |

**Why the parsing functions are extracted.** The monitor originally parsed `docker ps` output inside the same function that ran it, which makes it impossible to test without Docker. Separating `get_containers()` (effect) from `parse_ps()` (pure function) allows testing the parsing against inputs you can't reproduce in practice: `--` and `n/a` where numbers should be, missing columns, truncated lines, CPU above 100% (normal on multi-core), memory in GiB versus MiB, and `unhealthy` containing `healthy`.

**The most valuable tests are the ones written after each mistake.** There's one asserting that a manifest from an earlier version inherits the new keys. Another asserting that a failed push still writes the local heartbeat. Another asserting that the purge deletes nothing when the aggregate is missing. None of the three would have occurred to me before getting it wrong.

---

## What it really cost, and what's still open

All of this was one afternoon. And most of it wasn't writing code: it was reading data that had been sitting there for months without anyone looking at it.

What was left open that afternoon, and where it stands today:

| Open then | Today |
| --- | --- |
| The root cause of the container that took 59% of the recoveries | Still unknown. It hasn't happened again, and if it does, the evidence is frozen at the moment so it can be investigated later |
| Four relays with no coverage of any kind | All 26 current relays are checked every few minutes, and any failure reaches the alarm panel |
| Heartbeats covered one task out of nine | 36 tasks with heartbeats, all declared in the manifest; a stale heartbeat is an alarm |
| No consistency check on the metrics themselves | There's a consistency checker and a coverage inventory (below) |

That afternoon ended with an uncomfortable sentence: **every problem on this page was found by a person noticing something odd.** I saw the health step out of the corner of my eye looking at a sample. I caught the push that wasn't being sent because of three words missing from a line. None was caught by a system designed to catch it. The next section is what was built to change that.

---

## Three things I'd tell my March self

**Investigate before compressing.** The April five-minute detail was aggregated in the morning; the analysis of the 49 restarts was done that same afternoon. The hourly averages were enough to reach a conclusion, but the minute-by-minute sequence (which would have said exactly how long the container lasted between crashes) was lost permanently. Retention policies are irreversible in a way almost no other operation is.

**A green number isn't a correct number.** The three indicators had been in the daily report for months, in plain sight, every morning. None was broken. Each measured something real. None measured what its label promised. And precisely because they were green, nobody looked twice: green is a state nobody investigates.

**Discovery-based monitoring doesn't see absences.** It's convenient because there's no list to maintain and it adapts on its own when you add components. That same convenience is what turns a component's disappearance into silence, and silence into a report that says `20/20`.

---

## After that afternoon

The three findings had the same shape: an indicator nobody questioned because it was green. What came next were four pieces, each designed to find that shape without needing luck.

### An alarm panel, instead of a chat

Telegram alerts are a history: they say what **happened**. What was needed was a place that says what **is wrong now**. Every monitor writes its state, and an alarm panel in the monitoring portal gathers them: containers, heartbeats, relays, home automation, disks, system services, agent tasks, consistency, security. An alarm disappears when the problem disappears, not when someone reads the message.

It's the difference between the degraded mirror that alerted once and stayed that way for eleven days (it's in [Decisions and mistakes](../decisions.md)) and a system where that state stays red until it's fixed.

### Remediation with evidence, and an Apprentice

The verified-restart healer grew in two directions.

**Towards more kinds of failure.** Remediation now has a closed list of reversible actions (restart a container, restart a system service, rotate a log that grows out of control) and for each episode it freezes the evidence (state, logs, Docker events, restart history) and asks a model just one thing: **does any of these actions fix it?** The model doesn't decide what it may do; only whether something on the list applies, and why. If it says no, the reason is written down: "the container didn't start because Docker couldn't access the mount's disk; restarting it wouldn't fix it" is a diagnosis, not a remediation failure.

**Towards what isn't on the list.** The Apprentice watches the alarm panel. The first time a failure appears it stores the evidence when it appears and when it's resolved, and notes how it was fixed; with that it proposes a **recipe**, which a person confirms. The next time, with the recipe confirmed, it wakes the agent to apply it, checks whether the alarm clears, and respects a circuit breaker of three attempts a day.

The circuit breaker had its own lesson: during the antifragility tests it opened on *correct* repairs, because it counted a fix that worked the same as one that didn't. Now what gets repaired during an experiment doesn't count, and the message says "went down N times" instead of "failed".

### Consistency and inventory

**Consistency** cross-checks every hour the lists that should match each other: the reverse proxy routes, the home page's portals, what the security portal watches, the expected heartbeats, the loaded system services, the containers. A new portal without a route, a declared heartbeat for a task that no longer exists, a proxy target where nobody is listening: each one is an alert. Deliberate exceptions are written in a file, with their reason.

**The coverage inventory** asks every component (containers, services, home automation entities, tasks, relays) three questions: does it have a **declared expected state**? Is someone **watching** it? Would a failure **reach the panel**? Whatever doesn't answer all three is a gap even if it's green. It's the systematic version of the question that uncovered the relays: *five of what*.

### Antifragility: breaking it on purpose

Everything above answers "would this failure be seen?". It doesn't answer "what really happens when it occurs?". For that there's a programme of **induced failures**: a catalogue of just over a hundred experiments in five levels, from killing a container to cutting the Internet for an hour or pulling the Mac's plug, which are caused **by hand, one by one** (never an automatic orchestrator), with the family warned when they affect their services.

Each run measures how long it took to **detect**, how long to **repair**, and **who** repaired it: Docker, the operating system, remediation, the Apprentice with a recipe, the boot script, nobody because the system held up, or a person. A human repair scores low even if it's fast: the goal is for it not to be needed.

In the first six days there were **154 runs across 101 experiments**, and **56 learnings** were written down. A few that say more than the index:

| Experiment | What was expected | What happened |
| --- | --- | --- |
| Kill a container with `docker kill` | Docker relaunches it | **Docker doesn't relaunch after a manual kill**: it treats it as a voluntary stop. It took four attempts (and tuning margins, frequency and the circuit breaker) for it to repair itself in two minutes |
| Freeze a process with `kill -STOP` | Something detects it | **Nobody saw it.** Alive for the system, no heartbeat, invisible. Heartbeats had to be added to two processes that didn't have them |
| Stop the monitoring panel | It alerts and gets repaired | Serious finding: remediation had been **excluding three containers for two months**, the panel among them. Once, the model returned a malformed answer; that attempt was left "not evaluated", and the rule that avoids evaluating the same thing twice didn't look at the date: it never expired. And the Apprentice, which reads the panel, went blind without warning |
| Cut the Internet for an hour | Local keeps working | Local, perfect. But the home automation's cloud integrations went down and, when the connection came back, one monitor sent thirteen alerts at once about things that had fixed themselves |
| Pull the Mac's plug | It boots and recovers on its own | **Four attempts.** The first took 19 minutes because of an old alarm; the second found that the outage left an application stuck; the third, that the data disk mounted late and a dozen services failed to start without it; the fourth, all green in just over three minutes with nobody doing anything. Along the way it turned out the external observer couldn't alert during the outage |

The last row is the summary of the whole programme: **every experiment that goes wrong is a fix that wouldn't otherwise exist**, because none of those failures can be seen by reading configurations. They only show up by breaking things.

### The underlying lesson, again

The afternoon of the three findings ended saying that a green number isn't a correct number. Induced failures added the second half: **a system that recovers in the tests you imagined isn't a system that recovers**. The only way to know whether it recovers is to bring it down, as close as possible to how it will really go down, and look at what arrives, to whom and when.
