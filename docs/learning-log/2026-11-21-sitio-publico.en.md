---
title: A bilingual site on its own domain
---

# A bilingual site on its own domain

**Project:** P0 · **Date:** 21 Nov 2026 · **Hours:** 4

## What I built

My personal site for learning projects as an AI engineer. It is published on my domain `miquel.cleverhans.es`, which points to one of my GitHub repositories.

## The pieces and why

### Bilingual with `mkdocs-static-i18n`

`page.md` holds the page content and `page.en.md` its English translation. If a translation is missing, the Spanish original is shown.

### Pages generated from YAML with MkDocs hooks

The hook is a Python file that MkDocs runs while building the site: it reads the data in `cursos.yaml`, writes the table and turns it into a readable page that gets published. When I change a value in the YAML, the page regenerates itself in both languages.

### Own domain: DNS, wildcard and exceptions

I have the wildcard `*.cleverhans.es`, but it points to my house. That is why it doesn't work: this subdomain has to point to GitHub, so it needs an explicit CNAME in Cloudflare as an exception. Inside the house the same happens with AdGuard's wildcard, so it needs its own exception.

### GitHub Pages with Actions

`deploy.yml` builds the site, packages it as a zip and hands it to GitHub, which publishes it. Since GitHub doesn't look inside the package to configure itself, the domain has to be set in Settings → Pages: the repo's `CNAME` file is not enough.

## Alternatives ruled out

Serving it from the homelab. We host it on GitHub so it doesn't depend on my homelab environment: the idea is to take it out of there.

## What surprised me

The whole planning of the learning project: one year to improve my AI Engineer skills.
