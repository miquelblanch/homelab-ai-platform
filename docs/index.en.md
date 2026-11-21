# Homelab AI Platform

A self-hosted AI platform on a single Mac Mini M4 Pro. It serves my home, but it is built and run with the practices I would apply to a production system: instrumented, tested, documented and honest about what it does badly.

**51 containers. An agent that nothing essential depends on. About 2,000 indexed pages. Zero cloud dependencies for what matters, and almost a hundred failures caused on purpose to check that it recovers.**

---

## What it does

**A conversational agent on Telegram.** It queries the knowledge base, operates the infrastructure and the house, and steps in when judgment is needed: after a power cut or when a failure has no known recipe. It uses a cloud model with a cheaper fallback that kicks in on its own if the main one fails. Everything essential (monitoring, repairing, backing up, reporting) works without it.

**A hybrid-search RAG** over about 2,080 pages and 11,200 chunks. Vector search (HNSW) fused with lexical search (BM25) through reciprocal rank fusion. Embeddings run locally on `bge-m3`.

**Infrastructure that heals itself.** About sixty in-house services watch containers, disks, relays, home automation, DNS and security, and feed a central alarm panel. Each failure is repaired automatically when there is a safe action for it (the restart **is verified**, a zero exit code is not enough) and a circuit breaker stops the loop when things start failing in cascade. Anything fixed by hand twice becomes a recipe.

**Backups that are checked, not assumed.** A nightly copy with a consistent dump of every database, a second copy with history, and seventeen checks the next morning. The report arrives whether it passes or fails. And backups are really restored, in scripted drills.

**Failures caused on purpose.** An antifragility programme with almost a hundred experiments, from killing a container to cutting the Internet for an hour or pulling the Mac's power, that measures how long each failure takes to detect and repair, and turns every surprise into a fix.

---

## Why this is a case study

Almost everything written about homelabs is an inventory: a list of containers and a dashboard screenshot. This is about the hard, transferable part: **finding out that your monitoring is lying to you**.

While instrumenting the metrics pipeline, three indicators that had been green for months turned out to measure something other than what their label promised:

| Indicator | What it seemed to say | What it actually measured |
| --- | --- | --- |
| Auto-recovery: 100% success | restarts fix the problem | that `docker restart` returned 0 |
| Container health ratio | share of healthy containers | everything not explicitly broken |
| Relays 5/5 | the nine relays work | five checks pass, three unrelated |

None of them was broken. Each measured something real. None measured what it promised.

That is the thread running through this documentation: the gap between a system that reports green and a system that actually works, and the engineering needed to close it.

---

## Where to start

<div class="grid cards" markdown>

- **[Architecture](architecture.md)**

    How the pieces fit: the agent runtime, the RAG engine, the monitoring layer, and why the container network needs relays to reach physical devices.

- **[The agent](case-studies/agent.md)**

    Cost-based model routing, tool calling against real infrastructure, human approval, and the three ways a local 9B model failed silently.

- **[The RAG](case-studies/rag.md)**

    Hybrid retrieval and rank fusion, local embeddings, and why the hard part of a RAG is not search but ingestion.

- **[Reliability](case-studies/reliability.md)**

    Metrics persistence, verified recovery, circuit breakers, retention with aggregation, and heartbeats for scheduled jobs.

- **[Security audit](case-studies/security.md)**

    An active audit of the whole platform: 37 findings, eleven of them critical, and a control that reported the exact opposite of what was happening.

</div>

If you only read one page, read **[Decisions and mistakes](decisions.md)**. It is the shortest, and the only one where things go wrong.

!!! note "Translation in progress"
    This English version is being translated page by page. Pages not yet translated are shown in Spanish.

---

## Constraints worth knowing

This is a single-node system, with no redundancy, on consumer hardware and a flat home network. It is designed for a house, not a company. Several decisions that look odd (relays to reach the LAN, a TCP proxy in front of the reverse proxy, SQLite instead of a time-series database) come straight from those constraints, and each one is argued where it appears.

Addresses, domains and host names in this documentation are illustrative.
