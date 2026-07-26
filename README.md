# Homelab AI Platform

A self-hosted AI platform on a single Mac Mini M4 Pro: a Telegram agent with cost-aware model routing, a hybrid-search RAG over ~2,000 documents, and 41 containers with instrumented, verified auto-remediation.

**📖 [Read the full case study →](https://miquelblanch.github.io/homelab-ai-platform/)**

---

## The short version

While instrumenting the metrics pipeline, three indicators that had been green for months turned out to measure something other than what their labels claimed:

| Indicator | What it appeared to say | What it actually measured |
| --- | --- | --- |
| Auto-remediation: 100% success | restarts fix the problem | `docker restart` returned exit code 0 |
| Container health ratio | share of healthy containers | everything not explicitly broken |
| Relays 5/5 | all nine relays are up | five checks pass, three of them unrelated |

None of them was broken. None measured what it promised. Closing that gap is what this project is about.

## What's here

```
docs/    the case study, built with Material for MkDocs
src/     the operational code, sanitized: monitoring, metrics,
         remediation, retention, heartbeats, and their tests
```

## Stack

Python 3.11, standard library only for the operational scripts · Docker via OrbStack · SQLite · PostgreSQL with pgvector · Ollama (`nomic-embed-text`) · LM Studio (`qwen3.5-9b`) · DeepSeek · Home Assistant · Immich · Jellyfin · Nextcloud · Obsidian · Traefik · CrowdSec · Beszel · Uptime Kuma

## Running the docs locally

```bash
pip install -r requirements.txt
mkdocs serve
```

## A note on what is not here

This documents a system that runs in my home. Network addresses, domains, hostnames, port mappings and any detail about externally exposed surface are withheld or illustrative. The software is named; the topology is not. Nothing in this repository is a working configuration for my actual network, by design. The engineering is the point; the inventory is not.
