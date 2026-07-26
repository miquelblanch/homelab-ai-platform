# Decisions and mistakes

Every other page describes a system that works. This one is about how it got there, which was less tidy.

---

## Decisions that held up

Each of these is written with the condition under which it stops being right, because a decision without a reversal condition is a preference.

**The agent runs natively, not in a container.**
It manages the container runtime. Putting it inside the thing it manages creates a loop where it cannot restart itself, and the failure mode is the worst kind: it only appears when something has already gone wrong.
*Reverse it when* the agent no longer touches the runtime.

**SQLite instead of Prometheus.**
One node, a five-minute interval, a few dozen series. SQLite means no extra containers, no scrape config, no retention subsystem, and a query language I already know. Prometheus and Grafana would each need monitoring of their own.
*Reverse it at* roughly ten nodes, or the first time I want alerting rules expressed over the metrics themselves rather than over the raw checks.

**Local model for scheduled jobs, cloud model for interactive.**
Scheduled work runs unattended and often; its cost has to be zero. Interactive work has a human waiting, so quality wins.
*Reverse it per job* — which is exactly what happened for multi-tool jobs, below.

**Monitors never raise.**
Every function in the persistence and heartbeat layers catches its own exceptions and returns a neutral value. In application code this would be bad practice. In a monitor, an unhandled exception means you stop hearing about *everything else*, which is strictly worse than losing one sample.
*Reverse it* nowhere. The alternative is a monitor that dies quietly.

**Aggregate before deleting.**
Retention downsamples to hourly averages before pruning raw rows, and the prune only touches hours that already exist in the aggregate. The safety net lives in the SQL, not in the caller.
*Reverse it* never. This one is cheap insurance against the class of mistake you only make once.

**The audit is a script, not a document.**
The document was the deliverable I asked for. The script is the artefact that still has value a year later, because it can run again and produce a number.

---

## Mistakes

### The relay that stole its own port

A `socat` relay was set up to forward `0.0.0.0:PORT` to `127.0.0.1:PORT` so a dashboard would be reachable from the LAN. Same port on both sides.

The dashboard was already listening on `127.0.0.1:PORT`. And the relay started first at boot.

So after every reboot, the relay bound the port, the dashboard failed to start with "address already in use", and the relay dutifully forwarded traffic to itself. The log line repeated forever. The dashboard was down for however long it took me to notice, once per reboot, for weeks.

**What changed:** the relay was deleted, and the dashboard binds the LAN interface itself. The plist is archived with a note saying *do not recreate*, because the failure looks exactly like a port conflict caused by something else.

**The lesson is not "check your ports".** It is that a component whose whole job is indirection will happily point at itself, and nothing in the tooling will object.

---

### A metrics writer nobody missed

A component was dismantled. It had been writing container metrics to a database table. Nothing else read that table.

Seventy days later I went looking for the data and found 265,365 rows, the most recent one from ten weeks earlier, and no code anywhere in the repository that wrote to it. It had not broken. It had been deleted along with its owner, and the absence produced no signal because nothing depended on it.

**What changed:** the monitor that was already running got persistence, and the scheduled job that maintains it got a heartbeat, so a second disappearance would be noticed.

**Lesson:** data nobody reads stops being produced, and you find out when you need it.

---

### A 100% success rate that measured exit codes

The auto-remediation system reported that every one of its 83 interventions had succeeded.

It was recording success when `docker restart` returned exit code 0, which happens whenever the daemon accepts the command — not when the container stays up. During two hours in April, one container sat at zero memory and zero CPU across twelve consecutive samples while the healer logged four successful restarts.

**What changed:** restarts are verified against `docker inspect` ten seconds later, and a circuit breaker stops the loop after three attempts in six hours.

**Cost:** one container consumed 59% of all automated interventions in the system for seven weeks without ever raising an alert, because the healer was working.

Full write-up in [Reliability](case-studies/reliability.md).

---

### A control that reported the opposite of reality

SSH was configured for key-only authentication. `sshd -T` reported that password authentication was enabled. Password authentication actually worked, because with PAM in the path it can authenticate regardless of that directive.

Three different answers, and the one that mattered was the one nobody had asked for: what happens when someone tries.

**What changed:** `AuthenticationMethods publickey`, verified by attempting a password login and being refused.

**And the part that stings:** my audit script had the same bug as my configuration. It checked `sshd -T`, so after the fix it kept reporting a failure. The control had to be corrected alongside the system.

Full write-up in [Security review](case-studies/security.md).

---

### "Never restart" quietly became "never check"

One container is deliberately switched off. It was added to a `NEVER_RESTART` list so the monitor would leave it alone.

The monitor implements that by skipping the container entirely — before any check runs. Which means a container that is supposed to be off can be running for days and nothing reports it. Which is what happened: four days, healthy, unnoticed, until I read a raw `docker ps` for an unrelated reason.

**What changed:** so far, only the documentation, which now says explicitly that this container has no monitoring and its state should be verified occasionally. The proper fix — a category that means *observe but never act* — is not built.

**Lesson:** exclusion lists conflate two different intentions. "Do not act on this" and "do not look at this" are not the same instruction, and collapsing them creates a blind spot that grows quietly.

---

### Hardening broke the monitoring, and the monitoring was wrong

Enabling the firewall's stealth mode stopped the host replying to ICMP. The external uptime monitor checked it by ping, and immediately reported the main server as down.

The reflex is to turn stealth mode off, because the monitor is now lying and the monitor is the thing making noise.

**What changed:** the monitor moved to a TCP check against a port that is genuinely serving. A ping proves a network stack answers ICMP; a TCP check proves something is listening, which is closer to what "up" was supposed to mean.

**Lesson:** when a security control breaks an operational control, the operational one is usually the one that was measuring the wrong thing. But it is the one complaining, so it tends to win the argument.

---

### I introduced a bug and shipped its fix the same day

Instrumenting container health, I recorded `healthy` for anything not explicitly `unhealthy`. Docker's actual field is *empty* when a container defines no health check — and empty is not healthy, it is unchecked.

The result was a step change in the time series on the day I added it, caused entirely by my own code. 491 of 779 rows were wrong: 63%.

**What changed:** the value is derived from the stored status string, and a migration corrected the existing rows. The check that made the fix trustworthy was not that it ran, but that the proportions matched across the boundary — 37.4% historically against 37.0% after the correction.

**Lesson:** a migration that runs cleanly and produces plausible garbage is the default outcome. Validate against something you did not just compute.

---

### Defaults that never reached an existing config

The heartbeat system reads a manifest declaring which jobs are expected. When I later added an option to also push to the external monitor, the loader returned the existing file unchanged — so the new key simply did not exist in an already-created manifest.

The push was never sent. Both verification commands reported success: one because it bypassed the manifest, the other because the local write had worked. I caught it because a three-word suffix was missing from a line of output.

**What changed:** the loader merges defaults into missing keys and rewrites the file. And the status messages now say what they *did not* do, instead of silently omitting it.

**Lesson:** an output that only reports what happened cannot tell you about what did not.

---

### I compressed the evidence before I analysed it

Retention aggregated April's five-minute detail into hourly averages in the morning. That afternoon, investigating a container that had restarted 49 times, I needed the minute-by-minute sequence.

The hourly averages were enough to reach a conclusion. The full sequence is gone permanently.

**Lesson:** investigate before you compress. Retention policies are irreversible in a way that most operations are not.

---

## The pattern

Reading these back, most of them are the same mistake wearing different clothes.

| | It reported | It measured |
| --- | --- | --- |
| Remediation success | the problem was fixed | an exit code |
| Container health | healthy | not explicitly broken |
| Relay coverage | nine relays up | five unrelated checks passing |
| SSH configuration | key-only | a config file, not a login attempt |
| Heartbeat push | sent | that a local file was written |
| Scheduled jobs | all present | all *loaded* jobs present |

Six indicators. None broken. Each measured something real. **None measured what its label promised**, and every one of them was green.

The common shape is that each control measured the thing that was easy to measure — an exit code, a config file, a list of loaded units — and the label described the thing I cared about. The gap between those two is invisible precisely because the indicator is green, and green is not a state anyone investigates.

The uncomfortable corollary: every item on this page was found by a person noticing something odd. Not one was caught by a system built to catch it. Building that system is the obvious next piece of work, and it is not done.
