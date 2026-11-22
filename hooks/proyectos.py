"""MkDocs hook: project timeline chart and table from data/proyectos.yaml.

Same pattern as hooks/progreso.py: the page keeps a `<!-- proyectos -->`
marker and this hook replaces it at build time, in the language being built.

The chart is an inline SVG built here in Python: no JavaScript and no charting
library. Its colours come from CSS classes in docs/stylesheets/extra.css, so it
follows the light and dark themes.
"""

from __future__ import annotations

import datetime as dt
import html
from pathlib import Path

import yaml

DATA = Path(__file__).resolve().parent.parent / "data" / "proyectos.yaml"
MARKER = "<!-- proyectos -->"
ESTADOS = ("pendiente", "en_curso", "hecho")

MESES = {
    "es": ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"],
    "en": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
}

TEXT = {
    "es": {
        "resumen": "**{hechos} de {n}** proyectos terminados · **{horas} de {plan} h** dedicadas ({pct} %). Actualizado el {act}.",
        "proyecto": "Proyecto",
        "estado": "Estado",
        "plan": "Plan",
        "horas": "Horas",
        "resultado": "Resultado",
        "hoy": "hoy",
        "estados": {"pendiente": "Pendiente", "en_curso": "En curso", "hecho": "Hecho"},
        "leyenda_plan": "Plan",
        "leyenda_real": "Real",
        "aria": "Línea temporal de los proyectos del roadmap: plan frente a fechas reales",
        "real": "real: {ini} – {fin}",
    },
    "en": {
        "resumen": "**{hechos} of {n}** projects done · **{horas} of {plan} h** spent ({pct} %). Updated {act}.",
        "proyecto": "Project",
        "estado": "Status",
        "plan": "Plan",
        "horas": "Hours",
        "resultado": "Result",
        "hoy": "today",
        "estados": {"pendiente": "Planned", "en_curso": "In progress", "hecho": "Done"},
        "leyenda_plan": "Plan",
        "leyenda_real": "Actual",
        "aria": "Timeline of the roadmap projects: plan versus actual dates",
        "real": "actual: {ini} – {fin}",
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


def _short(d: dt.date, lang: str) -> str:
    return f"{d.day} {MESES[lang][d.month - 1]}"


def _month_label(d: dt.date, lang: str) -> str:
    m = MESES[lang][d.month - 1]
    return f"{m} {str(d.year)[2:]}" if d.month == 1 or d.month == 11 else m


def chart(data: dict, lang: str, today: dt.date) -> str:
    t = TEXT.get(lang, TEXT["es"])
    ps = data["proyectos"]
    dates = [p[k] for p in ps for k in ("plan_inicio", "plan_fin", "inicio", "fin") if p.get(k)]
    start = min(dates).replace(day=1)
    last = max(dates)
    end = dt.date(last.year + (last.month == 12), last.month % 12 + 1, 1)

    left, right, top, row = 205, 64, 34, 32
    width = 780
    plot = width - left - right
    height = top + row * len(ps) + 34
    span = (end - start).days

    def x(d: dt.date) -> float:
        return left + plot * (d - start).days / span

    out = [
        f'<svg class="gantt" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="{html.escape(t["aria"])}" xmlns="http://www.w3.org/2000/svg">',
        f"<title>{html.escape(t['aria'])}</title>",
        '<defs><linearGradient id="gantt-grad" x1="0" x2="1" y1="0" y2="0">'
        '<stop offset="0" class="gantt-stop-a"/><stop offset="1" class="gantt-stop-b"/>'
        "</linearGradient></defs>",
    ]

    # Month grid and labels.
    m = start
    while m <= end:
        xm = x(m)
        out.append(f'<line class="gantt-grid" x1="{xm:.1f}" y1="{top - 6}" x2="{xm:.1f}" y2="{height - 30}"/>')
        if m < end:
            nxt = dt.date(m.year + (m.month == 12), m.month % 12 + 1, 1)
            mid = (x(m) + x(nxt)) / 2
            out.append(f'<text class="gantt-axis" x="{mid:.1f}" y="{top - 12}" text-anchor="middle">{_month_label(m, lang)}</text>')
        m = dt.date(m.year + (m.month == 12), m.month % 12 + 1, 1)

    for i, p in enumerate(ps):
        y = top + i * row
        name = html.escape(f"{p['id']} · {p[lang]}")
        if len(p[lang]) > 22:
            name = html.escape(f"{p['id']} · {p[lang][:21].rstrip()}…")
        if p["estado"] == "en_curso":
            out.append(f'<rect class="gantt-band" x="4" y="{y + 2}" width="{width - 8}" height="{row - 4}" rx="6"/>')
        out.append(f'<text class="gantt-label" x="{left - 10}" y="{y + row / 2 + 4:.1f}" text-anchor="end">{name}</text>')
        px0, px1 = x(p["plan_inicio"]), x(p["plan_fin"])
        out.append(
            f'<rect class="gantt-plan" x="{px0:.1f}" y="{y + 6}" width="{max(px1 - px0, 3):.1f}" '
            f'height="{row - 12}" rx="4"><title>{t["plan"]}: {_short(p["plan_inicio"], lang)} – {_short(p["plan_fin"], lang)}</title></rect>'
        )
        if p.get("inicio"):
            real_end = p.get("fin") or max(today, p["inicio"])
            rx0, rx1 = x(p["inicio"]), x(real_end)
            cls = "gantt-real gantt-real--done" if p["estado"] == "hecho" else "gantt-real"
            tip = t["real"].format(ini=_short(p["inicio"], lang), fin=_short(real_end, lang))
            out.append(
                f'<rect class="{cls}" x="{rx0:.1f}" y="{y + 10}" width="{max(rx1 - rx0, 6):.1f}" '
                f'height="{row - 20}" rx="3"><title>{tip}</title></rect>'
            )
        hours = f"{p['horas']:g}/{p['horas_plan']:g} h"
        out.append(f'<text class="gantt-hours" x="{width - right + 8}" y="{y + row / 2 + 4:.1f}">{hours}</text>')

    # Today marker.
    if start <= today <= end:
        xt = x(today)
        out.append(f'<line class="gantt-today" x1="{xt:.1f}" y1="{top - 4}" x2="{xt:.1f}" y2="{height - 30}"/>')
        out.append(f'<text class="gantt-today-label" x="{xt:.1f}" y="{height - 16}" text-anchor="middle">{t["hoy"]}</text>')

    # Legend, bottom right (the today label sits bottom left of the plot).
    ly = height - 12
    lx = width - right - 150
    out.append(f'<rect class="gantt-plan" x="{lx}" y="{ly - 9}" width="18" height="10" rx="3"/>')
    out.append(f'<text class="gantt-axis" x="{lx + 24}" y="{ly}">{t["leyenda_plan"]}</text>')
    out.append(f'<rect class="gantt-real" x="{lx + 75}" y="{ly - 9}" width="18" height="10" rx="3"/>')
    out.append(f'<text class="gantt-axis" x="{lx + 99}" y="{ly}">{t["leyenda_real"]}</text>')
    out.append("</svg>")
    return "".join(out)


def _bar(done: float, plan: float) -> str:
    pct = min(100, round(100 * done / plan)) if plan else 0
    return (
        f'<span class="course-progress"><span class="course-bar"><span style="width:{pct}%"></span></span>'
        f"&nbsp;{done:g}/{plan:g}&nbsp;h</span>"
    )


def render(data: dict, lang: str, today: dt.date | None = None) -> str:
    today = today or dt.date.today()
    t = TEXT.get(lang, TEXT["es"])
    ps = data["proyectos"]
    horas = sum(p["horas"] for p in ps)
    plan = sum(p["horas_plan"] for p in ps)
    pct = round(100 * horas / plan) if plan else 0
    out = [
        t["resumen"].format(
            hechos=sum(p["estado"] == "hecho" for p in ps), n=len(ps),
            horas=f"{horas:g}", plan=f"{plan:g}", pct=pct, act=data["actualizado"],
        ),
        "",
        f'<div class="gantt-wrap">{chart(data, lang, today)}</div>',
        "",
        f"| {t['proyecto']} | {t['estado']} | {t['plan']} | {t['horas']} | {t['resultado']} |",
        "| --- | --- | --- | --- | --- |",
    ]
    for p in ps:
        estado = f'<span class="pstate pstate--{p["estado"]}">{t["estados"][p["estado"]]}</span>'
        window = f'{_short(p["plan_inicio"], lang)} – {_short(p["plan_fin"], lang)} {p["plan_fin"].year}'
        res = p.get("resultado", {}).get(lang, "")
        res = html.escape(res)
        if p.get("url") and res:
            res = f"[{res}]({p['url']})"
        out.append(
            f"| **{p['id']}** · {html.escape(p[lang])} | {estado} | <span class=\"nowrap\">{window}</span> | "
            f"{_bar(p['horas'], p['horas_plan'])} | {res} |"
        )
    return "\n".join(out) + "\n"


def on_page_markdown(markdown, page, config, files):
    if MARKER not in markdown:
        return markdown
    return markdown.replace(MARKER, render(load(), config["theme"]["language"]))


if __name__ == "__main__":
    data = load()
    for lang in ("es", "en"):
        r = render(data, lang)
        assert "<svg" in r and r.count("gantt-label") == len(data["proyectos"])
    print(f"ok: {len(data['proyectos'])} projects")
