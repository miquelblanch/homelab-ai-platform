# Security audit

> What happens when you audit a system you built yourself, and it turns out several controls were reporting on configuration rather than behaviour.

!!! info "What this page contains and what it doesn't"
    It describes the **method and the findings by category**, plus two incidents already closed that are worth reading for *how* they were found. It contains no reproducible detail, no current configuration, and nothing about what is exposed to the internet. Everything specific named here was fixed before it was written.

---

## Why I did it

The platform had grown by accumulation. It's the usual pattern in any system you build for yourself: a new container when a service is needed, a port opened when something doesn't work first time, a rule added to the reverse proxy that solves that afternoon's problem. Each isolated decision is reasonable. The sum of fifty isolated decisions, made over a year, was designed by nobody.

**Nothing had failed.** That's exactly the moment to look. After an incident you look because you have to, and you look biased towards what just broke.

The scope was everything: the host, the forty-odd containers, the services running inside them and the router. An **active audit**, not a checklist read from a document: commands run against the live system, output collected in a file, findings triaged afterwards.

---

## Method

### The decision with the biggest payoff

The audit was written as **a single bash script of about 500 lines**, run as root against the live system, producing a single output file.

The alternative would have been a review session: looking at things, noting findings and writing a report at the end. It's quicker to start and produces the same document.

And it's the wrong decision, because **a manual review produces a document and a script produces a number you can regenerate**. Everything that came afterwards depends on that difference: the remediation sprints, the progress scoreboard, and above all the ability to check that a fix really landed rather than just appearing to.

A report describes the day you wrote it. A script describes today.

### The twelve areas

| Area | Examples of what was checked |
| --- | --- |
| Host baseline | Disk encryption, firewall state and mode, system integrity protection, code signature verification |
| Accounts | Local users, who has administrative privileges, remote access configuration |
| SSH | Allowed authentication methods, inventory of authorised keys, brute-force protection |
| Network | Listening sockets, which interfaces they publish on, which services require authentication |
| Containers | Privileged mode, Docker socket access, sensitive bind mounts, port publishing scope |
| Services | Inference API, automation platform, databases |
| TLS | Certificate validity, issuance and expiry |
| Logs | Which security events are logged and for how long |
| Scheduled tasks | Privileges and ownership of system-level jobs |
| Tooling | What vulnerability scanning coverage exists, if any |

Each block dumped its raw output to the file. Analysis and severity classification were done afterwards, on that dump, not during the run: separating collection from judgment avoids fixing things on the fly and losing the full picture.

---

## What came out

### The count

The first pass produced **37 findings**:

| Severity | Count |
| --- | --- |
| Critical | 11 |
| High | 10 |
| Medium | 8 |
| Already correct | 8 |

Eleven critical findings in a system I had built, used daily and considered reasonably well set up. It's worth pausing there for a moment: it isn't that I had done things carelessly. It's that **care in the moment doesn't accumulate**, and nothing had ever made me look at the whole.

### The three classes

Most of the critical findings fell into one of three patterns.

**Services listening on more interfaces than necessary.** Things published on every interface when they only needed loopback or a single network.

The origin is almost always the same: you set up a service, it doesn't work first time, you open the binding to rule out a network problem, it works, and there it stays. The temporary configuration that solves that afternoon's problem becomes the permanent one because it never bothers you again. Each case is defensible individually; together, the listening surface was considerably larger than I thought I had.

**Container privileges above what's needed.** Containers running with more capabilities than their function requires.

The origin is identifiable here too: the example `docker-compose` files projects publish tend to be permissive on purpose, so they work in as many environments as possible without anyone opening an issue. You copy them, it works, and you never read those lines again. Every `privileged: true` inherited from an example is a container that, if compromised, has much more surface to move from.

**Authentication gaps in internal services.** Components deployed on the premise that "this is only on the LAN".

It's a valid security model until one of the devices on that LAN is compromised: a TV with outdated firmware, a cheap home automation gadget, a laptop that also connects to other networks. From then on, "only on the LAN" means "no authentication" for whoever is already inside.

None of this is exotic. It's the ordinary consequence of a system that grew one `docker run` at a time.

### The plan

The fixes were organised into **four phases by criticality, with 26 numbered items**. The first phase was carried out immediately; the rest went in sprints over the following weeks.

Numbering them mattered more than it seems: it lets you say "R-19 done" instead of "I think I already fixed the keys thing", and it lets the script's scoreboard map to specific plan items.

---

## The finding worth reading: a control that lied

### The starting point

SSH was configured for **key-only** authentication. The configuration file said so. It said so in two different places: in an included file and in the main one.

The audit script checked it with `sshd -T`, which doesn't read the file but asks the daemon itself what its effective configuration is. It is, in theory, the source of truth: it resolves includes, applies precedence and tells you what it's really doing.

`sshd -T` reported `PasswordAuthentication yes`.

### The chain of hypotheses

**First: a `Match` block at the end of the file** overriding the directive. It's the most common cause of this symptom: `Match` blocks apply conditionally and override what came before. I looked at the end of the file. There wasn't one.

**Second: an include ordering problem.** The included file might be loading before the main file's line, or vice versa, with different precedence than expected. I checked the order. Both said the same thing, so precedence was irrelevant.

At this point I'd spent a while doing ever more ingenious configuration-file archaeology, with no result.

### The change of approach

The right thing at that point is to stop reading configuration and **test the behaviour**. Try exactly the thing that shouldn't work:

```bash
ssh -o PreferredAuthentications=password -o PubkeyAuthentication=no user@host "echo test"
```

Both options force the client to try only a password, explicitly disabling key authentication. If the configuration is correct, this must be rejected.

```
user@host's password:
test
```

It asked for the password, accepted it, and ran the command.

### The cause

**With PAM enabled, PAM can authenticate by password regardless of the `PasswordAuthentication` directive.**

The setting doesn't exactly lie: it controls SSH's *own* password authentication method. But when `UsePAM` is on (it is by default on practically every distribution) there's a second authentication path through the PAM modules, and that directive doesn't close it.

In other words: there were three different answers to the same question. The file said no. The effective configuration dump said yes. And the real behaviour, the only one that matters, also said yes, but for a reason neither source explained.

### The fix

The solution isn't to disable an option, it's to **declare the requirement**:

```
AuthenticationMethods publickey
```

This doesn't say "don't allow passwords". It says "the only acceptable method is public key", which closes every alternative path, PAM included.

And it's verified exactly the way it was found:

```
user@host: Permission denied (publickey).
```

### Two lessons, and the second one is the uncomfortable one

**A configuration check is not a security check.** The audit was asking *"what does the daemon say it does?"* when the question that matters is *"what happens when someone tries?"*. Those two questions had different answers for months, and the first one gave the reassuring answer.

**My audit script had the same bug as my configuration.** It read `sshd -T`. So after applying the fix, the script **kept reporting a failure**, because `sshd -T` still says `yes`: PAM is still there, it just can't authenticate any more because `AuthenticationMethods` prevents it.

The control was wrong before and stayed wrong after, now in the opposite direction. The checker had to be fixed along with the system, replacing the configuration read with a real behaviour test.

It's the same failure mode as everything on the [Reliability](reliability.md) page, arriving from a completely different domain: **an indicator that measures what's easy to measure instead of what you care about.** Reading a configuration file is trivial; attempting a login and checking that you're rejected requires thinking about the design of the check. I didn't see it was the same pattern until I found it in both places in the same month.

---

## When hardening broke monitoring

### What happened

Among the host hardening measures, the firewall's **stealth mode** was turned on, which makes the machine not respond to unsolicited requests, `ping` included. The goal is not to confirm your own existence to a network scan.

The external uptime monitor checked the host **by ping**.

The result was immediate: the monitor went red and reported the main server as down. It had been up the whole time, with all its services working, responding perfectly over SSH and HTTP.

### The argument you have with yourself

The reflex is to turn stealth mode off. The argument makes itself: *"it used to work, I changed this, and now it's broken; so the change is wrong"*. And the monitor is sending alerts, and alerts are annoying, while stealth mode is invisible.

And it's the wrong conclusion. **The operational control was the one that was wrong.**

A `ping` proves that a machine's network stack answers ICMP. That's not what we wanted to know. What we wanted to know is whether the machine is serving, and for that the right check is a **TCP connection to a port that's actually serving**: it proves the operating system is alive, the network works, and there's a process listening and accepting connections.

The monitor was switched to a TCP check. It went back to green, and now measures something closer to what its label promised.

### Why it's worth writing down

Because the reflex goes the wrong way systematically. **When a security control breaks an operational one, the one usually measuring wrong is the operational one**: the security control has just shown you a weakness in your monitoring that was there from the start.

But the operational one is the one making noise, with a red dashboard, sending notifications to your phone. So it tends to win the argument, and the way it wins is by weakening the security measure you just put in place.

---

## The scoreboard

### The numbers

Since the audit is a script, progress is a number and not a claim:

| Run | Checks | Pass | Fail |
| --- | --- | --- | --- |
| After phase one | 67 | 15 | 2 |
| After the hardening sprints | 81 | 27 | **0** |

"Checks" are the total number of checks the script runs; many are informational and just dump data. The pass and fail columns are the ones that give a verdict.

### The healthier of the two numbers

The check count **grew from 67 to 81**, and that's more significant than reaching zero failures.

It grew because fixing things reveals things worth checking. Fixing SSH authentication raises the question of which keys are authorised and from where. Installing brute-force protection raises whether its allowlist is right. Every fix opens new questions.

**A static set of checks stops finding anything the moment you've satisfied it.** If your audit has had the same 67 checks for a year and all of them are green, what you have isn't a secure system: it's an audit that has stopped learning.

### New capabilities

Alongside the fixes, three things were added that simply didn't exist:

**Brute-force protection** with a local network allowlist, so a mistyped password from home doesn't end up locking me out.

**Vulnerability scanning of container images**, which wasn't done at all before. It didn't last long, and retiring it is as valid a decision as introducing it: almost all the warnings were about official images I don't control, and the only possible action (updating) already happens automatically every week. A control that produces warnings you can't act on doesn't protect you; it trains you to ignore them.

**An external observer on separate hardware**, so that a total failure of the main host is even detectable. Before, if the machine went off, there was nothing that could say so.

---

## What was deferred, and why

The later phases of the plan are still in progress. They're the **structural items**: the ones that require changing how the network is laid out rather than a setting in a file.

They were deliberately sequenced last, for two reasons. Each one **risks breaking services that work**, with a blast radius that goes well beyond a single container. And none of them resolves a critical finding: they are defence-in-depth improvements, valuable, but they reduce the impact of a compromise rather than preventing it.

That sequencing is the part I'd defend in a review: **fix the critical and cheap first, and accept the structural items as known and planned instead of pretending the plan is complete.**

A remediation plan with every item marked done is usually a plan that was scoped down to what was easy. A risk accepted explicitly, with its reason written down and its place in the queue, is an engineering decision. A risk nobody has stated is an oversight.

---

## The audit's blind spot

### What it didn't cover

The audit covered system configuration, container posture, network exposure and service authentication.

It didn't cover **application-level secrets inside the operational scripts**.

### How it showed up

Months later, instrumenting monitoring for reasons that had nothing to do with security, I found API credentials hard-coded in **six Python scripts**. The same token repeated in all of them, in plain text, at the top of each file.

They were never published anywhere, and they were moved to a file loaded at runtime outside version control, with restrictive permissions. But the audit had passed clean over a system that contained them.

And it did so correctly: that category wasn't in its scope. The script checked file permissions and process privileges, not source code content.

### The two conclusions

It's not an execution failure. It's **what an audit is**: a snapshot bounded by a scope, taken at a specific moment. Two things follow from that, worth keeping in mind every time someone shows a green report.

**Scope is everything.** Whatever is outside it is invisible, and the report won't warn you about what it didn't look at. A clean result means "we found no problems in what we looked for", which is much less like "there are no problems" than it sounds.

**Systems drift after they're audited.** That's the decisive argument for the script over the report: a script can be run again. The report described April. The script can describe today.

Automated execution of the script was for months the item I most regretted not finishing, for precisely this reason. It got finished, and grew well beyond the script (below).

---

## What came next: continuous security

The audit was a snapshot. What replaced it is a **security portal** with a collector that runs every thirty minutes and **only reads**: it doesn't change configuration, it doesn't restart anything. Each pass produces a full snapshot, compares it with the previous one and computes a score.

### What it looks at

| Area | What it checks |
| --- | --- |
| The machine | Firewall and stealth mode, system integrity protection, key-only SSH, screen sharing, system and package updates |
| Exposure | Which ports listen and for whom, which containers publish ports, and an **anonymous request to each service's real API** to see whether it asks for credentials |
| From the Internet | The public IP tested from outside: the two open ports must lead to the reverse proxy with its certificate, and **none** of the forty-plus internal service ports may answer |
| The edge | More than thirty end-to-end tests: each portal answers from home and returns 403 from outside, the proxy sees the real IP, the shared blocklist works |
| Access | Failed login attempts on every service, from home and from the Internet, and brute-force patterns |
| The local network | Every device in the house, by physical address: a new one is an alert until someone says "it's mine" |
| Changes | Everything that changed since the previous snapshot: a new port, a container appearing, a service being retired |

Since the edge passes the real IP to the proxy, the shared blocklist has useful data: on the public showcase, the only page open to the Internet, just over half the traffic is bots, and the blocklist has more than sixteen thousand addresses banned.

### Findings that can't be silently ignored

Every finding that calls for action opens a task in the homelab's to-do list, and the task updates itself if the finding changes. Closing it without fixing it doesn't make the finding go away. The only way to remove it from the score without fixing it is to **accept the risk** explicitly, with a mandatory note explaining why.

It's the same idea as the audit plan (a risk accepted with its reason written down is a decision; a risk nobody has stated is an oversight) but applied every half hour instead of once.

### What makes it different from the audit

The April audit found 37 findings and then stood still. The collector finds things **because the system changes**: the new device plugged in yesterday, the port an update opened, the service that lost its login after being recreated. Its finding rate doesn't tend to zero, and that, for the reason given in the scoreboard, is the healthy sign.

---

## What I'd do differently

**Test behaviour before reading configuration.** The SSH issue would have come out in the first five minutes with a single login attempt, instead of after a chain of ever more convoluted hypotheses about includes and precedence. Configuration is a statement of intent; behaviour is the fact.

**Write the checker before the checklist.** The document was the deliverable I had asked for myself. The script is the artefact that's still worth something a year later. If I had to choose one of the two, I wouldn't hesitate.

**Expect the audit to break something.** It did, immediately, and the recovery taught more than the fix itself. Hardening a system means changing its behaviour, and some components depended on the previous behaviour without anyone having written it down anywhere. Budget for it instead of being surprised, and above all don't use the scare as an argument to revert the measure.
