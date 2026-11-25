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

Pace KPIs (under the station line), both computed at the `actualizado` date,
which is when the hours were last reported:

- Against the plan: the plan spreads each project's planned hours linearly
  over its planned window. "Done" is the same planned-hours equivalent the
  big percentage uses (progress x planned hours). The gap is the number of
  days between `actualizado` and the day the plan reaches what is done
  (positive = ahead).
- Estimated end at the current pace: planned hours still to do, divided by
  real hours per week since the roadmap started (earliest `inicio`). With
  only a few days of data that number swings wildly, so it is shown only
  after MIN_SEMANAS weeks; before that the tile says from which date.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import html
from pathlib import Path

import yaml

DATA = Path(__file__).resolve().parent.parent / "data" / "proyectos.yaml"
MARKER = "<!-- proyectos -->"
ESTADOS = ("pendiente", "en_curso", "hecho")
EN_CURSO_MAX = 95
MIN_SEMANAS = 4  # weeks of data before projecting an end date
EN_PLAN_DIAS = 7  # a gap smaller than this reads as "on plan"

MESES = {
    "es": ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
           "agosto", "septiembre", "octubre", "noviembre", "diciembre"],
    "en": ["January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December"],
}

TEXT = {
    "es": {
        "avance": "del roadmap completado",
        "detalle": "{hechos} de {n} proyectos terminados · {horas} h dedicadas de {plan} h previstas · actualizado el {act}",
        "estados": {"pendiente": "Pendiente", "en_curso": "En curso", "hecho": "Hecho"},
        "aria": "Avance del roadmap",
        "ahora": "Ahora",
        "regla": "Un proyecto terminado cuenta como 100 %; uno en curso, sus horas reales sobre las previstas; el avance total pondera cada proyecto por sus horas previstas.",
        "regla_ritmo": "Respecto al plan: lo hecho frente a lo que el plan tendría hecho a esa fecha, repartiendo las horas de cada proyecto de forma lineal en su ventana prevista. Fin estimado: las horas previstas que faltan divididas por las horas reales por semana desde el arranque; se muestra a partir de {min} semanas de datos.",
        "plan_k": "Respecto al plan",
        "adelantado": "{n} adelantado",
        "atrasado": "{n} atrasado",
        "en_plan": "En plan",
        "plan_sub": "{hecho} h hechas (equivalentes) · el plan llevaría {plan} h · a {act}",
        "fin_k": "Fin estimado al ritmo actual",
        "fin_v": "{mes} de {anyo}",
        "fin_sub": "{ritmo} h/semana de media (plan: {plan} h) · {dif} el plan ({fin})",
        "antes": "{n} antes que",
        "despues": "{n} después que",
        "igual": "igual que",
        "pronto_v": "A partir del {fecha}",
        "pronto_sub": "Con {dias} días de datos ({horas} h) la proyección no es fiable; se calcula con {min} semanas como mínimo.",
        "sin_horas": "Sin horas registradas todavía.",
        "semanas": ("semana", "semanas"),
        "dias": ("día", "días"),
    },
    "en": {
        "avance": "of the roadmap complete",
        "detalle": "{hechos} of {n} projects done · {horas} h spent of {plan} h planned · updated {act}",
        "estados": {"pendiente": "Planned", "en_curso": "In progress", "hecho": "Done"},
        "aria": "Roadmap progress",
        "ahora": "Now",
        "regla": "A finished project counts as 100 %; one in progress, its real hours over its planned hours; overall progress weights each project by its planned hours.",
        "regla_ritmo": "Against the plan: what is done versus what the plan would have done by that date, spreading each project's hours linearly over its planned window. Estimated end: planned hours still to do divided by real hours per week since the start; shown once there are {min} weeks of data.",
        "plan_k": "Against the plan",
        "adelantado": "{n} ahead",
        "atrasado": "{n} behind",
        "en_plan": "On plan",
        "plan_sub": "{hecho} h done (equivalent) · the plan would be at {plan} h · as of {act}",
        "fin_k": "Estimated end at the current pace",
        "fin_v": "{mes} {anyo}",
        "fin_sub": "{ritmo} h/week on average (plan: {plan} h) · {dif} the plan ({fin})",
        "antes": "{n} earlier than",
        "despues": "{n} later than",
        "igual": "same as",
        "pronto_v": "From {fecha}",
        "pronto_sub": "With {dias} days of data ({horas} h) a projection is not reliable; it needs at least {min} weeks.",
        "sin_horas": "No hours logged yet.",
        "semanas": ("week", "weeks"),
        "dias": ("day", "days"),
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


def done_hours(ps: list[dict]) -> float:
    """Planned-hours equivalent of what is done (what the big % measures)."""
    return sum(pct(p) * p["horas_plan"] / 100 for p in ps)


def plan_hours(ps: list[dict], day: dt.date) -> float:
    """Hours the plan has done by the end of `day` (linear in each window)."""
    total = 0.0
    for p in ps:
        dias = (p["plan_fin"] - p["plan_inicio"]).days + 1
        hechos = (day - p["plan_inicio"]).days + 1
        total += p["horas_plan"] * min(1.0, max(0.0, hechos / dias))
    return total


def ritmo(data: dict) -> dict:
    """Gap against the plan (days, + = ahead) and estimated end date."""
    ps = data["proyectos"]
    ref = _date(data["actualizado"])
    hecho = done_hours(ps)
    plan_hoy = plan_hours(ps, ref)
    p0 = min(p["plan_inicio"] for p in ps)
    p1 = max(p["plan_fin"] for p in ps)
    dias = [p0 + dt.timedelta(days=i) for i in range(-1, (p1 - p0).days + 1)]
    if abs(hecho - plan_hoy) < 0.05:
        gap = 0
    elif hecho > plan_hoy:  # first day the plan reaches what is done
        gap = next(((d - ref).days for d in dias if d > ref and plan_hours(ps, d) >= hecho - 1e-9),
                   (p1 - ref).days)
    else:  # last day the plan was still at or below what is done
        gap = next(((d - ref).days for d in reversed(dias) if d < ref and plan_hours(ps, d) <= hecho + 1e-9),
                   (p0 - ref).days)
    inicios = [p["inicio"] for p in ps if p.get("inicio")]
    arranque = min(inicios) if inicios else ref
    dias_datos = (ref - arranque).days + 1
    horas = sum(p["horas"] for p in ps)
    plan_total = sum(p["horas_plan"] for p in ps)
    r = {
        "ref": ref, "hecho": hecho, "plan_hoy": plan_hoy, "gap": gap, "plan_fin": p1,
        "dias_datos": dias_datos, "horas": horas, "fin": None, "ritmo": None,
        "desde": arranque + dt.timedelta(weeks=MIN_SEMANAS),
    }
    if dias_datos >= 7 * MIN_SEMANAS and horas > 0:
        r["ritmo"] = horas / (dias_datos / 7)
        r["fin"] = ref + dt.timedelta(weeks=(plan_total - hecho) / r["ritmo"])
    return r


def _span(dias: int, t: dict) -> str:
    """'3 weeks' / '5 days' for a positive number of days."""
    if dias < 14:
        return f"{dias} {t['dias'][dias != 1]}"
    n = round(dias / 7)
    return f"{n} {t['semanas'][n != 1]}"


def _fecha(d: dt.date) -> str:
    return d.strftime("%d-%m-%Y")


def render_ritmo(data: dict, lang: str) -> str:
    t = TEXT.get(lang, TEXT["es"])
    e = html.escape
    r = ritmo(data)
    g = r["gap"]
    if abs(g) < EN_PLAN_DIAS:
        estado, valor = "ok", t["en_plan"]
    elif g > 0:
        estado, valor = "ahead", t["adelantado"].format(n=_span(g, t))
    else:
        estado, valor = "late", t["atrasado"].format(n=_span(-g, t))
    plan_sub = t["plan_sub"].format(hecho=f"{r['hecho']:.0f}", plan=f"{r['plan_hoy']:.0f}", act=_fecha(r["ref"]))
    if r["fin"]:
        d = (r["plan_fin"] - r["fin"]).days
        dif = (t["igual"] if abs(d) < EN_PLAN_DIAS
               else t["antes" if d > 0 else "despues"].format(n=_span(abs(d), t)))
        fin_estado = "ok" if abs(d) < EN_PLAN_DIAS else ("ahead" if d > 0 else "late")
        fin_v = t["fin_v"].format(mes=MESES[lang][r["fin"].month - 1], anyo=r["fin"].year)
        fin_sub = t["fin_sub"].format(ritmo=f"{r['ritmo']:.1f}".replace(".", "," if lang == "es" else "."),
                                      plan=f"{data.get('ritmo_plan', 7):g}", dif=dif, fin=_fecha(r["plan_fin"]))
    elif r["horas"] == 0:
        fin_estado, fin_v, fin_sub = "wait", "—", t["sin_horas"]
    else:
        fin_estado = "wait"
        fin_v = t["pronto_v"].format(fecha=_fecha(r["desde"]))
        fin_sub = t["pronto_sub"].format(dias=r["dias_datos"], horas=f"{r['horas']:g}", min=MIN_SEMANAS)
    return (
        '<div class="pg-kpis">'
        f'<div class="pg-kpi pg-kpi--{estado}"><span class="pg-kpi-k">{e(t["plan_k"])}</span>'
        f'<span class="pg-kpi-v">{e(valor)}</span><span class="pg-kpi-s">{e(plan_sub)}</span></div>'
        f'<div class="pg-kpi pg-kpi--{fin_estado}"><span class="pg-kpi-k">{e(t["fin_k"])}</span>'
        f'<span class="pg-kpi-v">{e(fin_v)}</span><span class="pg-kpi-s">{e(fin_sub)}</span></div>'
        "</div>"
    )


RING_R = 15.9155  # circumference 100, so a stroke-dasharray is a percentage
CHECK = '<path d="M11.5 18.5l4.5 4.5 8.5-9" class="pg-check" fill="none" stroke="#fff" stroke-width="3"/>'


def _dot(p: dict) -> str:
    """Inline SVG for one station (36x36 viewBox)."""
    if p["estado"] == "hecho":
        body = f'<circle cx="18" cy="18" r="13" class="pg-dot-done" fill="#0f9d8a"/>{CHECK}'
    elif p["estado"] == "en_curso":
        body = (
            f'<circle cx="18" cy="18" r="{RING_R}" class="pg-ring-track" fill="none" stroke="#c3c8d4" stroke-width="3.6"/>'
            + (
                f'<circle cx="18" cy="18" r="{RING_R}" class="pg-ring-fill" fill="none" stroke="#2f4fb3" stroke-width="3.6" '
                f'stroke-dasharray="{pct(p)} 100" transform="rotate(-90 18 18)"/>'
                if pct(p) else ""
            )
            + '<circle cx="18" cy="18" r="6" class="pg-dot-now" fill="#2f4fb3"/>'
        )
    else:
        body = '<circle cx="18" cy="18" r="8" class="pg-dot-todo" fill="none" stroke="#c3c8d4" stroke-width="3"/>'
    return f'<svg class="pg-dot" viewBox="0 0 36 36" width="36" height="36" aria-hidden="true">{body}</svg>'


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
    out += [render_ritmo(data, lang), f'<p class="pg-detail">{e(detalle)}</p>', "</div>", '<div class="pg-rows">']
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
    out += [
        "</div>",
        f'<p class="pg-rule">{e(t["regla"])} {e(t["regla_ritmo"].format(min=MIN_SEMANAS))}</p>',
        "</div>",
    ]
    return "\n".join(out) + "\n"


def on_config(config):
    """Cache-bust extra.css: its URL carries a hash of its content.

    Without it, browsers and the CDN keep serving the previous stylesheet for
    a while after a deploy, and new markup (SVG without its styles) renders
    as black shapes until the cache expires.
    """
    css = Path(config["docs_dir"]) / "stylesheets" / "extra.css"
    if css.exists():
        h = hashlib.sha256(css.read_bytes()).hexdigest()[:10]
        config["extra_css"] = [
            f"{u}?v={h}" if str(u) == "stylesheets/extra.css" else u for u in config["extra_css"]
        ]
    return config


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
    r = ritmo(data)
    print(f"ok: {len(data['proyectos'])} projects, {overall(data['proyectos'])} % overall, "
          f"gap {r['gap']:+d} days, end {r['fin'] or 'from ' + str(r['desde'])}")
