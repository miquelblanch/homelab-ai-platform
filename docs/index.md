# Homelab AI Platform

A self-hosted AI platform running on a single Mac Mini M4 Pro. It serves my household, but it is built and operated with the practices I would apply to a production system: instrumented, tested, documented, and honest about what it gets wrong.

**41 containers. One agent. 1,991 documents indexed. Zero cloud dependencies for anything that matters.**

---

## What it does

**A conversational agent over Telegram.** Routes each request to a local `qwen3.5-9b` or a cloud model depending on complexity, runs scheduled jobs, queries the knowledge base, and operates the infrastructure — with human approval required before it touches anything critical.

**A hybrid-search RAG** over 1,991 documents and 10,762 chunks. Vector search (HNSW) fused with lexical search (BM25) through reciprocal rank fusion. Embeddings run locally on `nomic-embed-text`.

**Self-healing infrastructure.** Every five minutes, 41 containers and 3 disks are checked. Non-critical containers are restarted automatically and the restart is *verified*; critical ones are escalated to a human. A circuit breaker stops the loop when a container starts flapping.

**Backups that are checked, not assumed.** Nightly snapshot plus nine database dumps, followed by thirteen integrity checks the next morning. The report arrives whether it passed or failed.

---

## Why this exists as a case study

Most homelab write-ups are inventories: a list of containers and a screenshot of a dashboard. This one is about the part that is harder and more transferable — **finding out that your monitoring is lying to you**.

While instrumenting the metrics pipeline, three separate indicators that had been green for months turned out to measure something other than what their labels claimed:

| Indicator | What it appeared to say | What it actually measured |
| --- | --- | --- |
| Auto-remediation: 100% success | restarts fix the problem | `docker restart` returned exit code 0 |
| Container health ratio | share of healthy containers | everything not explicitly broken |
| Relays 5/5 | all nine relays are up | five checks pass, three of them unrelated |

None of them was broken. Each measured something real. None measured what it promised.

That is the thread running through this documentation: the difference between a system that reports green and a system that is actually working, and the engineering it takes to close the gap.

---

## Where to start

<div class="grid cards" markdown>

- **[Architecture](architecture.md)**

    How the pieces fit: the agent runtime, the RAG engine, the monitoring layer, and why the container network needs relays to reach physical devices.

- **[Reliability](case-studies/reliability.md)**

    Metrics persistence, verified remediation, circuit breakers, retention with downsampling, and heartbeats for scheduled work.

- **[Security review](case-studies/security.md)**

    An active audit of the whole platform: 37 findings, eleven of them critical, and a control that reported the opposite of what was actually happening.

</div>

If you only read one page, read **[Decisions and mistakes](decisions.md)**. It is the shortest and the only one where things go wrong.

---

## Constraints worth knowing

This is a single-node system with no redundancy, running in a flat home network on consumer hardware. It is designed for a household, not a company. Several choices that look unusual — relays for LAN access, a local model for scheduled jobs, SQLite instead of a time-series database — follow directly from those constraints, and each is argued where it appears.

Addresses, domains and hostnames throughout this documentation are illustrative.
