---
title: Un sitio bilingüe con dominio propio
---

# Un sitio bilingüe con dominio propio

**Proyecto:** P0 · **Fecha:** 21-11-2026 · **Horas:** 4

## Qué he construido

Mi sitio personal de aprendizaje de proyectos como ingeniero de IA. Se publica en mi dominio `miquel.cleverhans.es`, que apunta a uno de mis repositorios en GitHub.

## Las piezas y por qué así

### Bilingüe con `mkdocs-static-i18n`

`page.md` tiene el contenido de la página y `page.en.md`, la versión traducida al inglés. Si falta la traducción, se muestra el original en español.

### Páginas generadas desde YAML con hooks de MkDocs

El hook es un fichero en Python que MkDocs ejecuta al construir la web: lee los datos de `cursos.yaml`, escribe la tabla y la convierte en una página legible que se publica. Cuando cambio un dato del YAML, la página se regenera sola en los dos idiomas.

### Dominio propio: DNS, comodín y excepciones

Tengo el comodín `*.cleverhans.es`, pero apunta a mi casa. Por eso no sirve: este subdominio tiene que apuntar a GitHub, y hace falta un CNAME explícito en Cloudflare que haga de excepción. Dentro de casa pasa lo mismo con el comodín de AdGuard, así que necesita su propia excepción.

### GitHub Pages con Actions

`deploy.yml` construye la web, la empaqueta como un zip y se la entrega a GitHub, que la publica. Como GitHub no mira dentro del paquete para configurarse, el dominio hay que ponerlo en Settings → Pages: el fichero `CNAME` del repo no basta.

## Alternativas descartadas

Servirlo desde el homelab. Lo hacemos en GitHub para que no dependa de mi entorno homelab: la idea es sacarlo de ahí.

## Lo que me sorprendió

Toda la planificación del proyecto de aprendizaje: un año para mejorar mis skills de AI Engineer.
