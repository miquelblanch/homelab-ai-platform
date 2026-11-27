---
title: Un re-ranker con LLM
---

# Un re-ranker con LLM

**Proyecto:** P1 · **Fecha:** 27-11-2026

## Lo que me sorprendió

Me ha sorprendido la mejora del re-ranker: el MRR pasa de 0,818 (RRF) a 0,925. Mi hipótesis era ≥ 0,88 y la ha superado con creces. Mejoran sobre todo las preguntas débiles (12 de 13), a 0,0006 € por pregunta.

Lo que más me ha sorprendido es que, aun así, una pregunta empeoró mucho (q083, del 1.º al 8.º puesto). Al analizarla vimos que al re-ranker le pasamos un solo chunk por documento, y el del documento correcto era la introducción, sin la respuesta. Para mejorarlo, pasaría 2–3 chunks por documento.
