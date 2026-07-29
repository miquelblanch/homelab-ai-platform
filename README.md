# Homelab AI Platform

Una plataforma de IA autoalojada sobre un único Mac Mini M4 Pro: un agente de Telegram con enrutado de modelos por coste (a través de Hermes Agent), un RAG de búsqueda híbrida sobre ~2.000 documentos, y 41 contenedores con recuperación automática instrumentada y verificada.

**📖 [Leer el caso de estudio completo →](https://miquelblanch.github.io/homelab-ai-platform/)**

---

## La versión corta

Instrumentando el pipeline de métricas, tres indicadores que llevaban meses en verde resultaron medir algo distinto de lo que prometía su etiqueta:

| Indicador | Lo que parecía decir | Lo que medía en realidad |
| --- | --- | --- |
| Recuperación automática: 100% de éxito | los reinicios arreglan el problema | que `docker restart` devolvió 0 |
| Ratio de salud de contenedores | proporción de contenedores sanos | todo lo que no estaba roto explícitamente |
| Relays 5/5 | los nueve relays funcionan | cinco comprobaciones pasan, tres sin relación |

Ninguno estaba roto. Ninguno medía lo que prometía. Cerrar esa distancia es de lo que va este proyecto.

## Qué hay aquí

```
docs/    el caso de estudio, construido con Material for MkDocs
src/     el código operativo, saneado: monitorización, métricas,
         recuperación, retención, latidos y sus tests
```

## Stack

Python 3.11, solo biblioteca estándar en los scripts operativos · Docker sobre OrbStack · SQLite · PostgreSQL con pgvector · Ollama (`nomic-embed-text`) · LM Studio (`qwen3.5-9b`) · DeepSeek · Hermes Agent · Home Assistant · Immich · Jellyfin · Nextcloud · Obsidian · Traefik · CrowdSec · Beszel · Uptime Kuma

## Levantar la documentación en local

```bash
pip install -r requirements.txt
mkdocs serve
```

## Una nota final

Esto documenta un sistema que corre en un entorno privado. Las direcciones de red, dominios, nombres de host, mapeos de puertos y cualquier detalle sobre la superficie expuesta a internet están omitidos o son ilustrativos. El software se nombra; la topología no. La ingeniería es el objetivo; no el hacer un inventario de los elementos de la infraestructura.
