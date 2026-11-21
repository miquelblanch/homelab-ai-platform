# Homelab AI Platform

Self-hosted personal infrastructure on a single Mac Mini M4 Pro: **51 Docker containers** covering networking, home automation, media, productivity, photos and monitoring, plus a conversational agent on Telegram that operates them and a RAG that indexes my own documents. It serves my day-to-day life, but it is built and run with the practices I would apply to a production system: instrumented, tested, documented.

**📖 [Read the full case study →](https://miquel.cleverhans.es/)** (Spanish by default, [English version](https://miquel.cleverhans.es/en/))

---

## What's here

```
docs/    the case study, built with Material for MkDocs
         (page.md = Spanish, page.en.md = English translation)
src/     the operational code, sanitised: monitoring, metrics,
         recovery, retention, heartbeats and their tests
```

## Stack

Python 3.11, standard library only in the operational scripts · Docker on OrbStack · SQLite · PostgreSQL with pgvector · Ollama (`bge-m3`) · OpenAI with DeepSeek fallback · Hermes Agent · Home Assistant · Frigate · Immich · Jellyfin · Nextcloud · Obsidian · HAProxy · Traefik · CrowdSec · Beszel · Uptime Kuma

## Build the docs locally

```bash
pip install -r requirements.txt
mkdocs serve
```

The site is bilingual through [mkdocs-static-i18n](https://github.com/ultrabug/mkdocs-static-i18n): Spanish is served at `/` and English at `/en/`. A page without an `.en.md` translation falls back to Spanish.

## A final note

This documents a system running in a private environment. Network addresses, domains, host names, port mappings and any detail about the internet-facing surface are omitted or illustrative. The software is named; the topology is not. The engineering is the point, not an inventory of the infrastructure.
