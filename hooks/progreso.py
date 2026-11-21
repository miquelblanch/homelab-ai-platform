"""MkDocs hook: builds the roadmap progress page from data/progreso.yaml.

Same pattern as hooks/cursos.py: the page keeps a `<!-- progreso -->`
marker and this hook replaces it at build time, in the language being built.
"""

from __future__ import annotations

import html
from pathlib import Path

import yaml

DATA = Path(__file__).resolve().parent.parent / "data" / "progreso.yaml"
MARKER = "<!-- progreso -->"
MAX = 4

MESES = {
    "es": ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"],
    "en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
}

TEXT = {
    "es": {
        "resumen": "**{n4} de {n}** competencias en 4 · nivel medio **{media}** de 4 · objetivo: todas en 4 en {fin}. Actualizado el {act}.",
        "competencia": "Competencia",
        "hoy": "Hoy",
        "siguiente": "Próximo hito",
        "evidencia": "Evidencia actual",
        "conseguido": "Conseguido",
        "sin": "Sin evidencia todavía.",
        "detalle": "Qué significa el 4 en cada una",
        "escala": "Escala",
        "escala_txt": "0 sin evidencia · 1 lo he estudiado · 2 lo he usado en un proyecto propio · 3 en producción, mantenido y depurado · 4 diseñado con método, medido y publicado.",
        "nivel": "nivel {n} en {fecha}",
    },
    "en": {
        "resumen": "**{n4} of {n}** skills at 4 · average level **{media}** of 4 · goal: all at 4 by {fin}. Updated {act}.",
        "competencia": "Skill",
        "hoy": "Today",
        "siguiente": "Next milestone",
        "evidencia": "Current evidence",
        "conseguido": "Reached",
        "sin": "No evidence yet.",
        "detalle": "What level 4 means for each one",
        "escala": "Scale",
        "escala_txt": "0 no evidence · 1 studied · 2 used in an own project · 3 in production, maintained and debugged · 4 designed with method, measured and published.",
        "nivel": "level {n} by {fecha}",
    },
}


def load(path: Path = DATA) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    ids = set()
    for c in data["competencias"]:
        for key in ("id", "es", "en", "nivel", "objetivo", "cuatro"):
            if key not in c:
                raise ValueError(f"progreso.yaml: missing {key} in {c.get('id')}")
        if c["id"] in ids:
            raise ValueError(f"progreso.yaml: duplicated id {c['id']}")
        ids.add(c["id"])
        for k in ("nivel", "objetivo"):
            if not 0 <= c[k] <= MAX:
                raise ValueError(f"progreso.yaml: {k} out of range in {c['id']}")
        c.setdefault("hitos", {})
        c.setdefault("evidencia", [])
    return data


def _fecha(ym, lang: str) -> str:
    y, m = str(ym).split("-")
    return f"{MESES[lang][int(m) - 1]} {y}"


def _dots(n: int) -> str:
    on = '<span class="lvl lvl--on"></span>' * n
    off = '<span class="lvl"></span>' * (MAX - n)
    return f'<span class="lvl-dots" title="{n}/{MAX}">{on}{off}</span>&nbsp;{n}'


def _next(c: dict, lang: str, t: dict) -> str:
    for ym, lvl in sorted(c["hitos"].items(), key=lambda kv: str(kv[0])):
        if lvl > c["nivel"]:
            return t["nivel"].format(n=lvl, fecha=_fecha(ym, lang))
    return t["conseguido"] if c["nivel"] >= c["objetivo"] else ""


def _evidence(c: dict, lang: str, t: dict) -> str:
    if not c["evidencia"]:
        return f"<em>{t['sin']}</em>"
    parts = []
    for e in c["evidencia"]:
        txt = html.escape(e[lang])
        parts.append(f"[{txt}]({e['url']})" if e.get("url") else txt)
    return "<br>".join(parts)


def render(data: dict, lang: str) -> str:
    t = TEXT.get(lang, TEXT["es"])
    comps = data["competencias"]
    n4 = sum(1 for c in comps if c["nivel"] >= MAX)
    media = f"{sum(c['nivel'] for c in comps) / len(comps):.1f}"
    if lang == "es":
        media = media.replace(".", ",")
    out = [
        t["resumen"].format(
            n4=n4, n=len(comps), media=media,
            fin=_fecha(data["fin"], lang), act=data["actualizado"],
        ),
        "",
        f"| {t['competencia']} | {t['hoy']} | {t['siguiente']} | {t['evidencia']} |",
        "| --- | --- | --- | --- |",
    ]
    for c in comps:
        out.append(
            f"| **{html.escape(c[lang])}** | {_dots(c['nivel'])} | {_next(c, lang, t)} | {_evidence(c, lang, t)} |"
        )
    out += ["", f"<small>**{t['escala']}:** {t['escala_txt']}</small>", "", f"## {t['detalle']}", ""]
    for c in comps:
        out.append(f"- **{html.escape(c[lang])}**: {html.escape(c['cuatro'][lang])}")
    return "\n".join(out) + "\n"


def on_page_markdown(markdown, page, config, files):
    if MARKER not in markdown:
        return markdown
    return markdown.replace(MARKER, render(load(), config["theme"]["language"]))


if __name__ == "__main__":
    data = load()
    for lang in ("es", "en"):
        assert "|" in render(data, lang)
    print(f"ok: {len(data['competencias'])} skills")
