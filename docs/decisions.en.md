# Decisions and mistakes

The other pages describe a system that works. This one is about how it got there, which was a lot less tidy.

It is split in two halves. At the top, decisions, each with the condition under which it would stop being right, and one that reached that condition. At the bottom, thirteen mistakes, with what they cost and what changed.

---

## Early decisions

Each one comes with its rollback, because **a decision made without thinking about the rollback is a mistake**.

### The agent runs natively, not in a container

The agent manages the container runtime: it lists containers, reads their logs, restarts them. Putting it inside the thing it manages creates a circular dependency.

The failure mode is the worst kind: **it only shows up when something has already gone wrong**. While Docker works, you notice nothing. The day Docker has a problem, the agent that could diagnose it is down precisely because of that problem, and it can't restart itself either, because the restart mechanism is what's broken.

It is the same principle by which a monitoring system must not depend on the infrastructure it watches.

*Rollback when* the agent stops touching the container runtime.

### SQLite instead of Prometheus

One node, a five-minute interval, a few dozen series.

In that regime, SQLite means: no extra container to maintain, no scraping configuration, no retention subsystem to learn, a query language I already know, and a file that goes into the nightly backup without doing anything special.

Prometheus and Grafana would bring automatic target discovery, a query language designed for time series, and alerts expressed over the metrics themselves. All of that is worth a lot with many nodes. With one, it's two more services that **themselves need monitoring**, to solve a problem that today fits in a 3 MB file.

*Rollback around* ten nodes, or the first time I want alert rules computed over the series rather than over raw checks.

### Local model for scheduled tasks, cloud for interactive: reverted

Scheduled work runs unsupervised, often, and carries context. Its cost had to be zero or it would grow while nobody watched. Interactive work has a human waiting, so quality and latency come first.

*Rollback per task*, and the rollback came in full. First, multi-tool tasks went back to the cloud, because the local model failed silently (it's in [Architecture](architecture.md)). Then the single-tool ones stopped needing a model: they were reports better written in deterministic code. When the local model ran out of work, it left the machine.

It is the only decision on this list that reached its rollback condition, and the one that taught the most: the useful question wasn't *which model* for automatic work, but *whether automatic work needed a model*.

### Monitors never throw exceptions

Every function in the persistence and heartbeat layers catches its own exceptions and returns a neutral value.

In application code that would be bad practice, and among the worst: swallowing exceptions hides bugs and makes debugging impossible. In a monitor the equation flips. An uncaught exception kills the process, and that means **no longer hearing about anything else** until someone looks. Losing one metrics sample is trivial damage; losing all monitoring because a disk write failed is serious damage.

The contract is checked in the tests themselves: there are assertions that point the database at paths that can't be written and check that the function returns a neutral value instead of propagating.

*Rollback* never. The alternative is a monitor that dies without anyone noticing.

### Aggregate before deleting

Retention compresses detail into hourly averages before purging it, and the purge only touches hours that **already exist** in the aggregate. If aggregation fails, the purge deletes nothing.

The safety net lives in the SQL query itself, not in the code that calls it. That's deliberate: a check in the caller can be skipped by calling the functions in the wrong order, or from a new place that didn't know about it. A condition inside the query can't be bypassed.

*Rollback* never. It's cheap insurance against the kind of mistake you only make once, because the first time you've already lost the data.

### The audit is a script, not a document

The document was the deliverable I asked for. The script is the artefact that's still worth something a year later, because it can be run again and produces a number comparable with the previous one.

A report describes the day you wrote it. A script describes today. And systems drift.

---

## Mistakes

### The relay that stole its own port

**What I built.** A `socat` relay forwarding `0.0.0.0:PORT` to `127.0.0.1:PORT`, so that a dashboard that only listened on loopback would be reachable from the local network. The same port number on both sides.

**What was really happening.** The dashboard was already listening on `127.0.0.1:PORT`. And the relay, as a system service, started **earlier** in the boot process.

So on every reboot the sequence was: the relay comes up and takes the port on every interface, loopback included. The dashboard tries to start, finds the port taken, fails with `address already in use` and gives up. And the relay, still alive and doing its job, diligently forwards all incoming traffic **to itself**.

**How it showed.** The dashboard simply wasn't there. Its log repeated the same error line indefinitely. And the symptom, "address already in use", makes you think of a zombie process or another application holding the port, not of the component whose only job is to make that very dashboard reachable.

It lasted weeks, coming back on every reboot, until I read the relay's configuration file carefully and saw the same number twice.

**What changed.** The relay was removed, and the dashboard was configured to listen directly on the network interface, which is what should have been done from the start. The service file is archived with a note saying *do not recreate*, precisely because the symptom invites you to set up a relay again.

**The lesson isn't "check your ports".** It's that a component whose whole job is indirection will happily point at itself, and **nothing in the tooling will object**. `socat` has no way of knowing the destination is itself, nor any reason to check. Any layer of indirection (a proxy, a forward, a DNS alias, a redirect) allows cycles that are only found by reading the configuration with fresh eyes.

---

### A metrics writer nobody missed

**What happened.** A component was dismantled that, among other things, wrote container metrics to a SQLite table. Nothing else read that table.

**How I found out.** Seventy days later I went looking for historical data to have numbers to show. I found 265,365 rows and a latest date ten weeks earlier.

The first hypothesis was the obvious one: something broke. I went looking for the code that wrote there to see why it was failing, and a `grep` over the whole repository returned **zero results**. It wasn't that the writer was failing: it didn't exist. It had been deleted along with its owner.

**Why nobody noticed.** Because there was nothing to notice. No dashboard showed that data, no alert depended on it, no task read it. A table that stops receiving writes produces no symptom; it simply stands still, and still looks a lot like normal.

**What changed.** The monitor that was already running gained persistence, and the scheduled task that maintains that data gained a heartbeat, so a second disappearance would be noticed, because now something is actively waiting to hear from it.

**Lesson:** data nobody reads stops being produced, and you find out the day you need it. And no alert is possible for the disappearance of something nothing depends on, so the only defence is for something to depend on it on purpose.

---

### A 100% success rate that measured exit codes

**What it reported.** The automatic recovery system said its 83 interventions had succeeded. All 83.

**What it measured.** It recorded success when `docker restart` returned exit code 0, which happens as soon as the daemon **accepts** the command. It doesn't wait for the container to start, doesn't check it stays alive, and knows nothing of what happens next.

**The evidence.** For two hours in April, a container sat at zero memory and zero CPU for twenty-four consecutive samples; in other words, stopped. In those same two hours the healer logged four successful restarts.

**What changed.** Restarts wait ten seconds and then query `docker inspect` to know whether the container is really running. `success` now means *observed up ten seconds later*. And a circuit breaker stops the loop after three attempts in six hours, escalating to a human with the container's logs attached.

**Cost.** One container consumed 59% of all automatic interventions in the system for seven weeks **without raising a single alert**, because the healer worked and each recovery was notified as an isolated piece of good news.

Full write-up, with the data and the analysis, in [Reliability](case-studies/reliability.md).

---

### A control that reported the opposite of reality

**What I believed.** SSH was configured for key-only authentication. The file said so in two places.

**What the control said.** `sshd -T`, the daemon's own dump of its effective configuration and in theory the source of truth, reported that password authentication was enabled.

**What was really happening.** Password authentication worked. With PAM in the authentication path, PAM can authenticate regardless of that directive.

Three different answers to the same question, and the one that mattered was the one nobody had asked: **what happens when someone tries**. A single password login attempt would have given the answer in five minutes, instead of a chain of ever more convoluted hypotheses about `Match` blocks and include order.

**What changed.** `AuthenticationMethods publickey`, which declares the requirement instead of disabling an option, and therefore closes the alternative paths too. Verified by attempting a password login and being rejected.

**And the part that stings:** my audit script had the same bug as my configuration. It checked `sshd -T`, so **after the fix it kept reporting a failure**, because `sshd -T` still says the same thing; it just doesn't matter any more. The control was wrong before and stayed wrong after, in the opposite direction. It had to be fixed along with the system.

Full write-up in [Security audit](case-studies/security.md).

---

### "Never restart" quietly became "never look"

**What I wanted.** There's a container that's deliberately off. I added it to a `NEVER_RESTART` list so the monitor wouldn't bring it up every five minutes.

**How it's implemented.** The monitor skips that container entirely, with a `continue` before running any check. It's the most direct way of getting what I asked for.

**The consequence.** A container that should be off can run for days without anything reporting it. Which is what happened: four days up, healthy, invisible, until I read raw `docker ps` output for an unrelated reason.

I never found out what started it. And that's the point: **there's no record, because nothing was looking.**

**What changed.** At first, only the documentation. Then the good fix came: remediation has a **per-container mode** (*manual*, which observes, diagnoses and proposes but doesn't act, and *automatic*) completely separate from monitoring, which no longer skips anyone. And the coverage inventory checks, for each component, whether it has a declared expected state, whether something watches it and whether a failure would reach the dashboard. The `NEVER_RESTART` list still exists, empty: its last member, the cameras container, went back to always running.

**Lesson:** exclusion lists conflate two different intentions. **"Don't act on this" and "don't look at this" are not the same instruction**, and collapsing them creates a blind spot that also grows: every item you add to the list for the first reason inherits the second for free, and nobody notices because the list still carries the first one's name.

---

### Hardening broke monitoring, and monitoring was wrong

**What happened.** Turning on the firewall's stealth mode left the host not answering ICMP, which is exactly its purpose. The external uptime monitor checked it by ping, and immediately reported the main server as down.

The machine had been up the whole time, with all its services working.

**The temptation.** Turn stealth mode off. The argument makes itself: it used to work, I changed this, now it's broken. And the monitor is sending alerts, while the security measure is invisible.

**What changed.** The monitor moved to a TCP check against a port that actually serves. A ping proves that a network stack answers ICMP; a TCP check proves that the system is alive, the network works, and a process is accepting connections, which is much closer to what "up" was meant to mean.

**Lesson:** when a security control breaks an operational one, **the one usually measuring wrong is the operational one**. The security measure has just shown you a weakness in your monitoring that was there from the start.

But the operational one is the one with the red dashboard sending notifications to your phone, so it tends to win the argument. And the way it wins is by getting you to weaken the measure you just put in place.

---

### I introduced a bug and shipped its fix the same day

**What I wrote.** While instrumenting container health, I recorded `healthy` for everything that wasn't explicitly `unhealthy`.

**Why it's wrong.** Docker's health field is *empty* when the container defines no health check, because defining one is optional. Empty isn't healthy: it's **unchecked**. Confusing them turns a health metric into a "hasn't blown up yet" metric.

**What it caused.** A sharp step in the time series on the day I added it, caused entirely by my own code. Anyone looking at that chart six months from now would conclude something changed in the system that day. Nothing changed in the system.

**How I spotted it.** By chance, skimming a sample of the hourly aggregate and finding a column full of zeros where I expected variety.

**What changed.** The value is derived from the status text, which was stored in full, and a migration fixed the rows already written. **491 of 779 were wrong: 63%.**

**What makes the fix trustworthy** isn't that it ran without errors, but that the proportions match on both sides of the boundary: 37.4% in the history versus 37.0% after the fix. Two datasets generated months apart by different writers, agreeing within half a percentage point.

**Lesson:** a migration that runs cleanly and produces plausible garbage is the default outcome. Validate against something you didn't just compute yourself.

---

### Defaults that never reached an existing configuration

**What I built.** A heartbeat system that reads a manifest declaring which jobs are expected and their maximum age. The manifest is created with default values the first time it runs.

**What I added later.** An option so that the heartbeat, besides writing the local file, would notify an external monitor.

**What failed.** The loader returned the existing file as is. A manifest **already created** didn't have the new key, so the option didn't exist for it and the push was never sent.

**Why I didn't catch it.** Because both checks I ran were green, each for a different reason. The push test command worked, because it goes straight to the external monitor without reading the manifest. And the heartbeat write command said "recorded", because the local file was indeed written correctly.

Two checks, two successes, and nothing reaching its destination.

**How I caught it.** An output line was missing a three-word suffix that should have appeared if the push had been attempted.

**What changed.** The loader merges defaults into missing keys and rewrites the file, respecting anything edited by hand. And status messages now say explicitly what they **didn't** do, instead of silently leaving it out.

**Lesson:** output that only reports what happened can't tell you what didn't. If the message had said `push skipped (not configured)` instead of staying quiet, it would have been obvious at once.

And the underlying one: **defaults that are only applied at creation never reach existing installations**, which are precisely all the ones already working.

---

### The backup that hadn't deleted anything in months

**What I believed.** That the backup was a mirror of the data disk: whatever is deleted at the source disappears from the destination. That's what `rsync --delete` does, and there it was, on the command line.

**What was happening.** Two macOS system directories (the ones holding version history and temporary files) are unreadable even for the machine's owner. rsync couldn't open them and exited with code 23: partial transfer.

That was already invisible, because the script never looked at the exit code and its summary said `✅` no matter what. But the real damage was one line higher in the log:

```
IO error encountered -- skipping file deletion
```

**rsync disables the `--delete` phase when it hits an I/O error.** And its reasoning is right: if it couldn't read the whole source, deleting at the destination could wipe files that do exist but that it never got to see. It's a well-designed safeguard.

The consequence is that the backup stopped being a mirror and became an accumulation: everything that had ever existed, including what was deleted on purpose. And growing.

**How it came to light.** By accident, while fixing something else. I was removing a hard-coded credential from that same script, ran the backup to check the change didn't break it, and noticed an exit code that had been there for months.

**The fix, in three layers.** Exclude the two system directories, so the error goes away. Capture the exit code and interpret it: 0 and 24 are fine, anything else is a failure with an alert. And have the heartbeat read by the verifier record the real result, instead of always writing the same thing.

**And then the second problem appeared, which the first one had been hiding.** With deletion working again, rsync tried to remove an orphaned file and couldn't: it belonged to `root`. There were 114 like it, leftovers of some old backup run with elevated privileges. A single `sudo` from months earlier leaving files the normal process can't manage, with the symptom appearing much later and somewhere that doesn't suggest it.

**Lesson:** a silent safeguard is a silent failure. rsync did the right thing (stop deleting when in doubt) and reported it through the only channel nobody was reading: its exit code. **A component that degrades itself to protect you needs someone to check whether it has degraded**, exactly like the backup disk mirror.

---

### I compressed the evidence before analysing it

**What I did.** In the morning I implemented and ran the retention policy, which aggregated April's five-minute detail into hourly averages.

**What I needed in the afternoon.** Investigating the container that had restarted 49 times, the minute-by-minute sequence: how long it lasted between crashes, whether the pattern within each burst was regular, whether there was anything periodic.

**What I had.** Hourly averages. Enough to reach the conclusion (in fact the key evidence, the two whole hours at zero, came from there) but the fine sequence was permanently lost.

**Lesson:** investigate before compressing. Retention policies are **irreversible** in a way almost no other operation is. You can roll back a deployment, restore a database, undo a commit. You can't recover the resolution of a time series you've already aggregated.

---

### Twelve containers marked critical, six with no way to prove it

**What I built.** A monitor that sorts containers into three groups: those marked critical (databases, home automation manager, reverse proxy, the tunnel that exposes services to the internet) only raise alerts and are never touched automatically; the rest are restarted with verification and a circuit breaker. The intent of the "critical" label is that they get the strictest monitoring.

**What was really happening.** The health logic is *running and healthy*, and "healthy" is read from the status field Docker exposes, which can only say "unhealthy" if the container has a healthcheck defined. Without one, Docker never reports anything other than "running", so the monitor treats any process that's still alive as healthy. Auditing the twelve critical containers against their real configuration, six had no healthcheck at all: the file platform's database and cache, the platform itself, the home automation manager, the reverse proxy and the internet tunnel. Precisely the six that were supposed to be best watched could only report that the process had died completely. A service up but returning errors, or with its database hung, was indistinguishable from a healthy one.

**How it showed.** No direct symptom: nothing was down in the sense the monitor could see. It came to light while investigating, by another route, a real failure of a scheduled notification, and asking whether the system could have detected it. Looking at the configuration, the answer was no.

**What changed.** A healthcheck was added to all six, each with its image's native tool: the database's connection check script, a ping to the cache, an HTTP request to each platform's status endpoint, the reverse proxy's own check subcommand (it already had the endpoint enabled, just unused), and a port check for the one that exposed nothing simpler. Applied with an active maintenance window and verifying each container before moving to the next, because two of the six expose the system to the internet.

**The lesson isn't "add healthchecks".** It's that a "critical" label protects nothing on its own: it only changes what the system does *if* it detects a failure, never whether it can detect it. Any monitor built on Docker's native health field inherits the same limit: without an explicit probe per service, "running" and "healthy" are the same thing to the tool, even though they aren't in reality. And nothing complains about this: neither the monitor, which does exactly what it was asked with the data it has, nor the dashboard, which only reflects what the monitor sends it. The gap is silent until someone audits component by component, instead of trusting that the label means what it promises.

---

### Eleven days of warning nobody read

**What I built.** The backup disk was a two-disk mirror, and since a degraded mirror behaves exactly like a healthy one, I added a checker that alerts on Telegram if the set stops being complete. It was the textbook remedy for the textbook failure mode.

**What happened.** One of the disks, connected over USB, dropped out of the set. I rebuilt it, considered the matter closed, and that same day it was degraded again. For **eleven days** it ran on a single disk.

**What the checker did.** Exactly its job: it alerted, and kept logging the degraded state every five minutes.

**Why it didn't help.** Because it was a single message, arriving on the same day I already considered the problem solved, and the five-minute log was only seen by whoever went looking. An alert nobody reads is indistinguishable from an alert that doesn't exist.

**What changed.** The disk was retired and the mirror disappeared from the design (it's in [Architecture](architecture.md)). And the way of alerting changed: monitors hold back failures that fix themselves within two minutes and group what fails together, so the alert that matters doesn't arrive buried. Today everything that's wrong right now lives in an alarm panel, not in a chat history.

**Lesson:** the 49-restart problem (isolated messages that don't trigger any mental alarm) isn't exclusive to good-news messages. An alerting system also has to be measured by **what the human actually gets to read**.

---

### The only possible alert went through the machine that was off

**What I built.** An external observer on another machine, precisely for the case no local monitor can cover: the whole server going down.

**What was really happening.** Its alerts didn't go straight to Telegram. They went to a small alerting service that forwarded them… and that ran on the server. With the server off, the observer detected the outage perfectly and couldn't tell anyone.

**How it came to light.** Not in a review, but by **causing a power cut on purpose** and counting which alerts arrived and through where. Too many arrived when it came back, and none during the outage.

**What changed.** The observer now has a direct notification that doesn't touch the server; and its server monitors are grouped, so an outage produces one alert when it goes down and another when it comes back, not eight.

**Lesson:** an external observer's independence has to be checked **on the way out**, not only on the way in. And some failures only appear when you cause them: this one had been there since day one, and no reading of the configuration would have made it obvious.

---

## The pattern

Rereading all this, almost all of them are the same mistake in different clothes.

| | Reported | Measured |
| --- | --- | --- |
| Recovery success | that the problem got fixed | an exit code |
| Container health | healthy | not explicitly broken |
| Relay coverage | nine relays up | five unrelated checks passing |
| SSH configuration | key only | a file, not a login attempt |
| Heartbeat push | sent | that a local file had been written |
| Scheduled tasks | all present | all *loaded* tasks present |
| Daily backup | the backup mirrors the source | that the script reached the end |

Seven indicators. None broken. Each measured something real and measured it correctly. **None measured what its label promised**, and all were green.

### Why it always happens the same way

The common shape is that each control measures **what's easy to measure** and the label describes **what I care about**.

Reading an exit code is one line of code; checking that a service recovered requires waiting, querying the real state and deciding what "recovered" means. Reading a configuration file is trivial; attempting a login and verifying the rejection requires designing the test. Listing loaded tasks is one system call; knowing whether one is missing requires keeping a list of what's expected.

In each case there's a cheap version and an expensive one, the cheap one gets implemented first, and the label is written describing the intention rather than the implementation. Then nobody comes back.

### Why it isn't detected

Because **the gap between the two is invisible precisely when the indicator is green**. A control that fails gets investigated: you look, and in five minutes you find it was measuring the wrong thing. A control that passes is never looked at. Green isn't a state anyone investigates.

All of these had been in plain sight for months, in a report I read every morning.

### The uncomfortable corollary

Every point on this page was found by **a person noticing something odd**. I saw the health step out of the corner of my eye looking at a sample. I caught the push that wasn't being sent by three words missing from a line. The 49 restarts showed up because I was looking at a table for a different reason.

Not a single one was caught by a system built to catch it. Building that system was the obvious next job, and since then it's been done on three fronts:

- **Consistency.** A checker that every hour cross-references the lists that should match (proxy routes, portals, heartbeats, system services, what the security portal watches) and alerts on what doesn't.
- **Coverage inventory.** For each component, three questions: does it have a declared expected state? Does something watch it? Would a failure reach the dashboard? Anything without all three answers is a gap, even if it's green.
- **Antifragility.** Failures caused by hand, one by one, measuring how long each takes to detect and repair. It's the only way to find the kind of mistake in the last two sections, which isn't seen by reading configurations but by breaking things.

The corollary is still uncomfortable, but in another direction: the last two mistakes on this page weren't found by a person noticing something odd. They were found by an experiment.
