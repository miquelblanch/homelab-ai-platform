# Security review

> What happens when you audit a system you built yourself, and it turns out several controls were reporting on configuration instead of behaviour.

!!! info "What this page does and does not contain"
    This describes the **method and the findings by category**, plus two closed issues that are worth reading because of *how* they were found. It contains no reproducible detail, no current configuration, and nothing about what is exposed. Every specific issue named here was fixed before this was written.

---

## Why I did it

The platform had grown by accumulation. Containers were added when I needed them, ports were opened when something did not work, and the reverse proxy config had layers nobody had revisited. Nothing had gone wrong, which is exactly the condition under which you should look.

The scope was everything: the host, all containers, the services running inside them, and the router. Active audit, not a checklist read from a document — commands run against the live system, output collected, findings triaged.

---

## Method

The audit was a single bash script covering twelve areas, run as root against the live system, producing one output file.

| Area | Examples of what was checked |
| --- | --- |
| Host baseline | Disk encryption, firewall state, system integrity protection, code signing enforcement |
| Accounts | Local users, privilege, remote login configuration |
| SSH | Authentication methods, key inventory, brute-force protection |
| Network | Listening sockets, binding scope, service authentication |
| Containers | Privileged mode, socket exposure, bind mounts, port binding scope |
| Services | Inference API, automation platform, databases |
| TLS | Certificate validity and issuance |
| Logs | Security event logging and retention |
| Scheduled tasks | Privilege and ownership of system-level jobs |
| Tooling | Vulnerability scanning coverage |

Writing the audit as a **script rather than a session** was the single most useful decision. A manual review produces a document; a script produces a number you can regenerate. Everything downstream — the sprints, the scoreboard, the ability to confirm a fix actually landed — depends on the audit being repeatable.

---

## What came out

The first pass produced **37 findings**:

| Severity | Count |
| --- | --- |
| Critical | 11 |
| High | 10 |
| Medium | 8 |
| Already correct | 8 |

Eleven critical findings on a system I had built and considered reasonable. Most clustered into three classes:

**Services bound wider than they needed to be.** Things listening on all interfaces that only ever needed loopback or a single network. Individually defensible, collectively a much larger surface than intended.

**Container privilege.** Containers running with more capability than their function required, mostly inherited from example `docker-compose` files copied at setup time and never revisited.

**Authentication gaps on internal services.** Components deployed on the assumption that "it is only on the LAN", which is a security model right up until one device on that LAN is compromised.

None of these were exotic. They were the ordinary consequence of a system that grew one `docker run` at a time.

The remediation plan was organised into four phases by criticality, with 26 numbered items. Phase one was executed immediately; the rest ran as sprints.

---

## The finding worth reading: a control that lied

SSH was configured for key-only authentication. The config file said so, in two places. The audit script checked it with `sshd -T`, the daemon's own effective-configuration dump.

`sshd -T` reported `PasswordAuthentication yes`.

The obvious first hypothesis was a `Match` block overriding it at the bottom of the file. There was none. Second hypothesis: an include ordering problem. Also no.

At which point the right move is to stop reading configuration and **test the behaviour**:

```bash
ssh -o PreferredAuthentications=password -o PubkeyAuthentication=no user@host "echo test"
```

```
user@host's password:
test
```

Password authentication worked. The config said no, the effective-config dump said yes, and reality agreed with neither in a way that mattered: **with PAM enabled, PAM can authenticate by password regardless of the `PasswordAuthentication` directive.** The setting is not a lie, it is just not the whole authentication path.

The fix is to state the requirement rather than disable an option:

```
AuthenticationMethods publickey
```

Then verify the same way it was found:

```
user@host: Permission denied (publickey).
```

### Two lessons, and the second is the uncomfortable one

**A configuration check is not a security check.** The audit had asked "what does the daemon say it does" instead of "what happens when someone tries". Those had different answers for months.

**My audit script had the same bug as my configuration.** It read `sshd -T`, so after the fix it kept reporting a failure — the control was still wrong, now in the other direction. The script had to be corrected alongside the system.

That is the same failure mode as everything on the [Reliability](reliability.md) page, arriving from a completely different direction: **an indicator that measures the thing that is easy to measure rather than the thing you care about.** I did not notice the pattern until I had hit it in both domains.

---

## When hardening broke the monitoring

Part of the host hardening enabled the firewall's stealth mode, which stops the machine replying to ICMP.

The external uptime monitor checked the host by ping.

The monitor immediately reported the main server as down. It had been up the whole time.

The tempting fix is to turn stealth mode back off, because the monitor is now wrong and the monitor is annoying. The correct fix is the other one: **change the monitor to a TCP check against a port that is genuinely serving.** A ping proves a network stack answers ICMP. A TCP check proves something is listening, which is closer to what "up" was supposed to mean anyway.

This is worth writing down because the reflex goes the wrong way. When a security control breaks an operational control, the operational one is usually the one measuring the wrong thing — but it is the one making noise, so it wins the argument.

---

## The scoreboard

Because the audit was a script, progress is a number rather than an assertion:

| Run | Controls | Passing | Failing |
| --- | --- | --- | --- |
| After phase one | 67 | 15 | 2 |
| After the hardening sprints | 81 | 27 | **0** |

The control count grew because fixing things revealed things worth checking. That growth is the healthier of the two numbers: a static control set stops finding anything the moment you have satisfied it.

Alongside the fixes, three capabilities were added that did not exist before: brute-force protection with a LAN allowlist, container image vulnerability scanning, and an external observer running on separate hardware so that the total failure of the main host is detectable at all.

---

## What was deferred, and why

Later phases of the plan are still in progress. They are the structural items — the ones that require changing how the network is laid out rather than changing a setting — and they were deliberately sequenced last, because each one risks breaking working services and none of them addresses a critical finding.

That sequencing is the part I would defend in a review: **fix what is critical and cheap first, and accept the structural items as known and scheduled rather than pretending the plan is complete.** A remediation plan with everything marked done is usually a plan that was scoped to what was easy.

---

## The blind spot the audit had

The audit covered system configuration, container posture, network exposure and service authentication. It did not cover **application-level secrets inside operational scripts**.

Months later, instrumenting the monitoring, I found API credentials hardcoded in six Python scripts. They were never published anywhere and were moved to a runtime-loaded file outside version control, but the audit had run cleanly over a system that contained them, because that category was not in scope.

This is not a flaw in the execution. It is what an audit is: a snapshot bounded by a scope, taken at a moment. Two things follow.

**Scope is the whole ballgame.** Everything outside it is invisible, and the report will not say so.

**Systems drift after you audit them.** The reason the script matters more than the report is that a script can run again. The report described April; the script can describe today.

The weekly automated run is the item I most regret not having finished.

---

## What I would do differently

**Test behaviour before reading configuration.** The SSH issue would have surfaced in the first five minutes with one login attempt, instead of after a chain of increasingly clever config archaeology.

**Write the checker before the checklist.** The document was the deliverable I was asked for. The script is the artefact that still has value a year later.

**Expect the audit to break something.** It did, immediately, and the recovery was more instructive than the fix. Budget for it rather than being surprised.
