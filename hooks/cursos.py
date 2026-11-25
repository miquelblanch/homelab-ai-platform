"""MkDocs hook: builds the courses page from data/cursos.yaml.

Why a hook and not a plugin: hooks are a built-in MkDocs feature (a plain
Python file listed under `hooks:` in mkdocs.yml), so there is no extra
dependency to pin. The page keeps a `<!-- cursos -->` marker and this hook
replaces it with tables at build time, in the language being built.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data" / "cursos.yaml"
# Certificate images live in the repo, so "View" opens them on this site.
# File name = Udemy certificate code, e.g. docs/certificados/UC-xxxx.jpg.
CERT_DIR = ROOT / "docs" / "certificados"
CERT_CODE = re.compile(r"(UC-[0-9a-f-]+)")
MARKER = "<!-- cursos -->"
DONE = 98  # Udemy rarely reaches 100 %: >= 98 counts as completed.

TEMAS = {
    "llm": ("LLMs", "LLMs"),
    "evals": ("Evals", "Evals"),
    "agentes": ("Agentes", "Agents"),
    "mcp": ("MCP", "MCP"),
    "local": ("Modelos locales", "Local models"),
    "finetuning": ("Fine-tuning", "Fine-tuning"),
    "dl": ("Deep Learning", "Deep learning"),
    "ml": ("Machine Learning", "Machine learning"),
    "sdd": ("Spec-Driven Development", "Spec-Driven Development"),
    "python": ("Python", "Python"),
    "ingenieria": ("Ingeniería de software", "Software engineering"),
    "sistemas": ("Sistemas", "Systems"),
    "seguridad": ("Seguridad", "Security"),
    "datos": ("Datos", "Data"),
    "lowcode": ("Low-code (Power Platform)", "Low-code (Power Platform)"),
}

TEXT = {
    "es": {
        "en_curso": "En curso",
        "terminados": "Terminados",
        "terminados_id": "terminados",
        "previstos": "Previstos en el roadmap",
        "curso": "Curso",
        "tema": "Tema",
        "avance": "Avance",
        "fin": "Terminado",
        "certificado": "Certificado",
        "ver": "Ver",
        "cerrar": "Cerrar",
        "proyecto": "Proyecto",
        "resumen": "{t} terminados · {c} en curso · {p} previstos.",
        "vacio": "Ninguno por ahora.",
    },
    "en": {
        "en_curso": "In progress",
        "terminados": "Completed",
        "terminados_id": "completed",
        "previstos": "Planned in the roadmap",
        "curso": "Course",
        "tema": "Topic",
        "avance": "Progress",
        "fin": "Completed",
        "certificado": "Certificate",
        "ver": "View",
        "cerrar": "Close",
        "proyecto": "Project",
        "resumen": "{t} completed · {c} in progress · {p} planned.",
        "vacio": "None yet.",
    },
}


def load(path: Path = DATA) -> list[dict]:
    cursos = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    for c in cursos:
        if "titulo" not in c or "tema" not in c:
            raise ValueError(f"cursos.yaml: missing titulo/tema in {c}")
        if c["tema"] not in TEMAS:
            raise ValueError(f"cursos.yaml: unknown tema {c['tema']!r} in {c['titulo']!r}")
        c.setdefault("avance", 0)
        if c.get("certificado") and not str(c["certificado"]).startswith("https://"):
            raise ValueError(f"cursos.yaml: certificado must be an https URL in {c['titulo']!r}")
        if not 0 <= c["avance"] <= 100:
            raise ValueError(f"cursos.yaml: avance out of range in {c['titulo']!r}")
    return [c for c in cursos if c.get("mostrar", True)]


def group(cursos: list[dict]) -> tuple[list, list, list]:
    previstos = [c for c in cursos if c.get("comprado") is False]
    resto = [c for c in cursos if c.get("comprado") is not False]
    terminados = sorted(
        (c for c in resto if c["avance"] >= DONE),
        key=lambda c: str(c.get("fin", "")),
        reverse=True,
    )
    en_curso = sorted(
        (c for c in resto if c["avance"] < DONE),
        key=lambda c: (not c.get("plan"), -c["avance"]),
    )
    return en_curso, terminados, previstos


def _title(c: dict) -> str:
    t = html.escape(c["titulo"])
    autor = f" · {html.escape(c['autor'])}" if c.get("autor") else ""
    return f"**{t}**<br><small>{html.escape(c.get('plataforma', ''))}{autor}</small>"


def _cert_image(c: dict) -> str | None:
    """Local image of the certificate, if it is in docs/certificados/."""
    m = CERT_CODE.search(str(c.get("certificado", "")))
    if m and (CERT_DIR / f"{m.group(1)}.jpg").exists():
        return m.group(1)
    return None


def _cert(c: dict, label: str) -> str:
    url = c.get("certificado")
    if not url:
        return ""
    code = _cert_image(c)
    if code:
        return f'<a href="#cert-{code}">{label}</a>'
    return f'<a href="{html.escape(url)}" target="_blank" rel="noopener">{label}</a>'


def _cert_modal(c: dict, t: dict) -> str:
    """Pure CSS lightbox (opened with :target, styled in extra.css)."""
    code = _cert_image(c)
    if not code:
        return ""
    titulo = html.escape(c["titulo"])
    return (
        f'<div class="cert-modal" id="cert-{code}" role="dialog" aria-label="{titulo}">'
        f'<a class="cert-backdrop" href="#{t["terminados_id"]}" aria-label="{t["cerrar"]}"></a>'
        f'<figure><img src="/certificados/{code}.jpg" alt="{titulo}" loading="lazy">'
        f'<figcaption><a href="#{t["terminados_id"]}">{t["cerrar"]}</a></figcaption></figure></div>'
    )


def _bar(avance: int) -> str:
    # Pure CSS progress bar (styled in extra.css).
    return (
        f'<span class="course-progress"><span class="course-bar" title="{avance} %">'
        f'<span style="width:{avance}%"></span></span>&nbsp;{avance}&nbsp;%</span>'
    )


def render(cursos: list[dict], lang: str) -> str:
    t = TEXT.get(lang, TEXT["es"])
    idx = 1 if lang == "en" else 0
    tema = lambda c: TEMAS[c["tema"]][idx]  # noqa: E731
    en_curso, terminados, previstos = group(cursos)

    out = [t["resumen"].format(t=len(terminados), c=len(en_curso), p=len(previstos)), ""]

    out += [f"## {t['en_curso']}", ""]
    if en_curso:
        out += [f"| {t['curso']} | {t['tema']} | {t['avance']} | {t['proyecto']} |", "| --- | --- | --- | --- |"]
        out += [f"| {_title(c)} | {tema(c)} | {_bar(c['avance'])} | {c.get('plan', '')} |" for c in en_curso]
    else:
        out.append(t["vacio"])

    out += ["", f"## {t['previstos']}", ""]
    if previstos:
        out += [f"| {t['curso']} | {t['tema']} | {t['proyecto']} |", "| --- | --- | --- |"]
        out += [f"| {_title(c)} | {tema(c)} | {c.get('plan', '')} |" for c in previstos]
    else:
        out.append(t["vacio"])

    out += ["", f"## {t['terminados']}", ""]
    if terminados:
        out += [
            f"| {t['curso']} | {t['tema']} | {t['fin']} | {t['certificado']} |",
            "| --- | --- | --- | --- |",
        ]
        out += [
            f"| {_title(c)} | {tema(c)} | {c.get('fin', '')} | {_cert(c, t['ver'])} |"
            for c in terminados
        ]
        out += ["", *filter(None, (_cert_modal(c, t) for c in terminados))]
    else:
        out.append(t["vacio"])
    return "\n".join(out) + "\n"


def on_page_markdown(markdown, page, config, files):
    if MARKER not in markdown:
        return markdown
    lang = config["theme"]["language"]
    return markdown.replace(MARKER, render(load(), lang))


if __name__ == "__main__":
    # Self-test, run by CI: the YAML parses and both languages render.
    cursos = load()
    for lang in ("es", "en"):
        assert "|" in render(cursos, lang)
    for c in cursos:
        if c.get("certificado") and "udemy.com/certificate/" in str(c["certificado"]):
            assert _cert_image(c), f"missing docs/certificados image for {c['titulo']!r}"
    print(f"ok: {len(cursos)} courses")
