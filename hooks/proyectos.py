"""MkDocs hook: roadmap milestone line from data/proyectos.yaml.

Same pattern as hooks/progreso.py: the page keeps a `<!-- proyectos -->`
marker and this hook replaces it at build time, in the language being built.

Output is static HTML, CSS and a little inline SVG (no JavaScript): the
overall percentage as a big number, then a horizontal line with one station
per project in roadmap order, without dates. Finished stations are filled,
the current one carries a ring with its percentage, planned ones are grey.
Below it, a compact list with each project's result. Colours come from CSS
variables in docs/stylesheets/extra.css, so they follow the light and dark
themes.

Progress rule: a finished project counts as 100 %; one in progress counts its
real hours over its planned hours (capped at 95 % until it is marked done);
a planned one counts 0 %. Overall progress weights each project by its
planned hours.
"""

from __future__ import annotations

import datetime as dt
import html
from pathlib import Path

import yaml

DATA = Path(__file__).resolve().parent.parent / "data" / "proyectos.yaml"
MARKER = "<!-- proyectos -->"
ESTADOS = ("pendiente", "en_curso", "hecho")
EN_CURSO_MAX = 95

TEXT = {
    "es": {
        "avance": "del roadmap completado",
        "detalle": "{hechos} de {n} proyectos terminados · {horas} h dedicadas de {plan} h previstas · actualizado el {act}",
        "estados": {"pendiente": "Pendiente", "en_curso": "En curso", "hecho": "Hecho"},
        "aria": "Avance del roadmap",
        "ahora": "Ahora",
        "regla": "Un proyecto terminado cuenta como 100 %; uno en curso, sus horas reales sobre las previstas; el avance total pondera cada proyecto por sus horas previstas.",
    },
    "en": {
        "avance": "of the roadmap complete",
        "detalle": "{hechos} of {n} projects done · {horas} h spent of {plan} h planned · updated {act}",
        "estados": {"pendiente": "Planned", "en_curso": "In progress", "hecho": "Done"},
        "aria": "Roadmap progress",
        "ahora": "Now",
        "regla": "A finished project counts as 100 %; one in progress, its real hours over its planned hours; overall progress weights each project by its planned hours.",
    },
}


def _date(v) -> dt.date:
    return v if isinstance(v, dt.date) else dt.date.fromisoformat(str(v))


def load(path: Path = DATA) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    ids = set()
    for p in data["proyectos"]:
        for key in ("id", "es", "en", "estado", "plan_inicio", "plan_fin", "horas_plan", "horas"):
            if key not in p:
                raise ValueError(f"proyectos.yaml: missing {key} in {p.get('id')}")
        if p["id"] in ids:
            raise ValueError(f"proyectos.yaml: duplicated id {p['id']}")
        ids.add(p["id"])
        if p["estado"] not in ESTADOS:
            raise ValueError(f"proyectos.yaml: unknown estado {p['estado']!r} in {p['id']}")
        for k in ("plan_inicio", "plan_fin", "inicio", "fin"):
            if p.get(k) is not None:
                p[k] = _date(p[k])
        if p["plan_fin"] < p["plan_inicio"]:
            raise ValueError(f"proyectos.yaml: plan ends before it starts in {p['id']}")
        if p["estado"] != "pendiente" and not p.get("inicio"):
            raise ValueError(f"proyectos.yaml: {p['id']} is {p['estado']} but has no inicio")
        if p["estado"] == "hecho" and not p.get("fin"):
            raise ValueError(f"proyectos.yaml: {p['id']} is hecho but has no fin")
    return data


def pct(p: dict) -> int:
    """Progress of one project, 0-100."""
    if p["estado"] == "hecho":
        return 100
    if p["estado"] == "pendiente" or not p["horas_plan"]:
        return 0
    return min(EN_CURSO_MAX, round(100 * p["horas"] / p["horas_plan"]))


def overall(ps: list[dict]) -> int:
    plan = sum(p["horas_plan"] for p in ps)
    return round(sum(pct(p) * p["horas_plan"] for p in ps) / plan) if plan else 0


RING_R = 15.9155  # circumference 100, so a stroke-dasharray is a percentage
CHECK = '<path d="M11.5 18.5l4.5 4.5 8.5-9" class="pg-check"/>'


def _dot(p: dict) -> str:
    """Inline SVG for one station (36x36 viewBox)."""
    if p["estado"] == "hecho":
        body = f'<circle cx="18" cy="18" r="13" class="pg-dot-done"/>{CHECK}'
    elif p["estado"] == "en_curso":
        body = (
            f'<circle cx="18" cy="18" r="{RING_R}" class="pg-ring-track"/>'
            + (
                f'<circle cx="18" cy="18" r="{RING_R}" class="pg-ring-fill" '
                f'stroke-dasharray="{pct(p)} 100" transform="rotate(-90 18 18)"/>'
                if pct(p) else ""
            )
            + '<circle cx="18" cy="18" r="6" class="pg-dot-now"/>'
        )
    else:
        body = '<circle cx="18" cy="18" r="8" class="pg-dot-todo"/>'
    return f'<svg class="pg-dot" viewBox="0 0 36 36" aria-hidden="true">{body}</svg>'


def render(data: dict, lang: str) -> str:
    t = TEXT.get(lang, TEXT["es"])
    ps = data["proyectos"]
    total = overall(ps)
    detalle = t["detalle"].format(
        hechos=sum(p["estado"] == "hecho" for p in ps), n=len(ps),
        horas=f"{sum(p['horas'] for p in ps):g}", plan=f"{sum(p['horas_plan'] for p in ps):g}",
        act=data["actualizado"],
    )
    e = html.escape
    actual = next((p for p in ps if p["estado"] == "en_curso"), None)
    out = [
        f'<div class="pg" role="group" aria-label="{e(t["aria"])}">',
        '<div class="pg-hero">',
        f'<div class="pg-head"><span class="pg-big" role="img" aria-label="{total} %">{total}&nbsp;%</span>'
        f'<span class="pg-avance">{e(t["avance"])}</span></div>',
        '<ol class="pg-line">',
    ]
    for p in ps:
        tip = e(f"{p['id']} · {p[lang]} · {t['estados'][p['estado']]} · {pct(p)} %")
        num = f'<span class="pg-st-pct">{pct(p)}&nbsp;%</span>' if p["estado"] == "en_curso" else ""
        out.append(
            f'<li class="pg-st pg-st--{p["estado"]}" title="{tip}">{_dot(p)}'
            f'<span class="pg-st-id">{p["id"]}</span>{num}'
            f'<span class="sr-only">{e(p[lang])}: {t["estados"][p["estado"]]}, {pct(p)} %</span></li>'
        )
    out.append("</ol>")
    if actual:
        out.append(
            f'<p class="pg-now"><span class="pg-now-tag">{e(t["ahora"])}</span> '
            f'<strong>{actual["id"]} · {e(actual[lang])}</strong> · {pct(actual)}&nbsp;%</p>'
        )
    out += [f'<p class="pg-detail">{e(detalle)}</p>', "</div>", '<div class="pg-rows">']
    for p in ps:
        res = e(p.get("resultado", {}).get(lang, ""))
        if p.get("url") and res:
            res = f'<a href="{e(p["url"])}">{res}</a>'
        out.append(
            f'<div class="pg-row pg-row--{p["estado"]}">'
            f'<div class="pg-name"><span class="pg-id">{p["id"]}</span> {e(p[lang])}'
            + (f'<span class="pg-res">{res}</span>' if res else "")
            + "</div>"
            f'<span class="pstate pstate--{p["estado"]}">{t["estados"][p["estado"]]}</span>'
            f'<span class="pg-num">{pct(p)}&nbsp;%<small>{p["horas"]:g}/{p["horas_plan"]:g}&nbsp;h</small></span>'
            "</div>"
        )
    out += ["</div>", f'<p class="pg-rule">{e(t["regla"])}</p>', "</div>"]
    return "\n".join(out) + "\n"


def on_page_markdown(markdown, page, config, files):
    if MARKER not in markdown:
        return markdown
    return markdown.replace(MARKER, render(load(), config["theme"]["language"]))


if __name__ == "__main__":
    data = load()
    for lang in ("es", "en"):
        r = render(data, lang)
        assert r.count('class="pg-row ') == len(data["proyectos"])
        assert r.count('class="pg-st ') == len(data["proyectos"])
        assert "\n\n" not in r  # a blank line would end the raw HTML block
    print(f"ok: {len(data['proyectos'])} projects, {overall(data['proyectos'])} % overall")
