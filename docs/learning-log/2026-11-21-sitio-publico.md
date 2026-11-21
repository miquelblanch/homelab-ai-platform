---
title: Un sitio bilingüe con dominio propio
---

# Un sitio bilingüe con dominio propio

**Proyecto:** P0 · **Fecha:** 21-11-2026 · **Horas:** _(rellenar)_

!!! warning "Borrador"
    Las respuestas de cada apartado las escribe Miquel con sus palabras. Las preguntas guía se borran al publicar.

## Qué he construido

_Pregunta guía: si se lo contaras a un compañero de Grifols en dos frases, ¿qué es este sitio y cómo se publica?_

## Las piezas y por qué así

### Bilingüe con `mkdocs-static-i18n`

_¿Qué hace la estructura `page.md` / `page.en.md` y qué pasa si falta una traducción? ¿Por qué un plugin y no dos sitios separados?_

### Páginas generadas desde YAML con hooks de MkDocs

_¿Qué es un hook, cuándo se ejecuta y qué ventaja tiene frente a escribir las tablas a mano o a un plugin externo?_

### Dominio propio: DNS, comodín y excepciones

_¿Por qué `miquel.cleverhans.es` necesitó un CNAME en Cloudflare si ya había un comodín? ¿Por qué hizo falta además una reescritura en AdGuard? ¿Por qué la nube gris?_

### GitHub Pages con Actions

_¿Por qué el fichero `CNAME` no bastó y hubo que poner el dominio en Settings → Pages?_

## Alternativas descartadas

_Servir el sitio desde el homelab con nginx + Traefik. ¿Por qué no?_

## Lo que me sorprendió

_¿Qué no esperabas? (Pista: `dig` contra tu AdGuard frente a `dig @1.1.1.1`.)_

## Si lo volviera a hacer

_Una cosa que harías distinta._
