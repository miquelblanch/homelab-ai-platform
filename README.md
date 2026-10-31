# Homelab AI Platform

Infraestructura personal autoalojada en casa, sobre un único Mac Mini M4 Pro: **49 contenedores Docker** que cubren red, domótica, media, productividad, fotos y monitorización, más un agente conversacional por Telegram que los opera y un RAG que indexa mis propios documentos. Da servicio a mi día a día, pero está construida y operada con las prácticas que aplicaría a un sistema en producción — instrumentada, testeada, documentada.

**📖 [Leer el caso de estudio completo →](https://miquelblanch.github.io/homelab-ai-platform/)**

---

## Qué hay aquí

```
docs/    el caso de estudio, construido con Material for MkDocs
src/     el código operativo, saneado: monitorización, métricas,
         recuperación, retención, latidos y sus tests
```

## Stack

Python 3.11, solo biblioteca estándar en los scripts operativos · Docker sobre OrbStack · SQLite · PostgreSQL con pgvector · Ollama (`bge-m3`) · OpenAI con reserva en DeepSeek · Hermes Agent · Home Assistant · Frigate · Immich · Jellyfin · Nextcloud · Obsidian · HAProxy · Traefik · CrowdSec · Beszel · Uptime Kuma

## Levantar la documentación en local

```bash
pip install -r requirements.txt
mkdocs serve
```

## Una nota final

Esto documenta un sistema que corre en un entorno privado. Las direcciones de red, dominios, nombres de host, mapeos de puertos y cualquier detalle sobre la superficie expuesta a internet están omitidos o son ilustrativos. El software se nombra; la topología no. La ingeniería es el objetivo; no el hacer un inventario de los elementos de la infraestructura.
