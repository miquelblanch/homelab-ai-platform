#!/usr/bin/env python3
"""
heartbeat.py — Latidos de las tareas programadas del homelab.

Por qué existe
--------------
La monitorización de LaunchAgents funciona **por descubrimiento**: informa
sobre los agentes que están cargados. Si un plist no se carga, se descarga,
o falla el bootstrap tras un reinicio, el agente simplemente desaparece del
recuento y todo sigue en verde. Para un job semanal eso significa que puede
pasar un mes sin ejecutarse sin que nada avise.

Un latido invierte la pregunta: en vez de "¿está el agente cargado?",
comprueba "¿se hizo el trabajo, y hace cuánto?". Cubre también el caso de
un agente cargado que falla en silencio.

Mismo patrón que `.backup-heartbeat`, generalizado.

Uso desde un script
-------------------
    python3 heartbeat.py --write metrics-retention
    python3 heartbeat.py --write metrics-retention --status error --detail "exit 1"

Uso desde código
----------------
    import heartbeat
    heartbeat.write("metrics-retention")
    heartbeat.report()   # [(job, ok, edad_h, status, detail), ...]

Comprobación
------------
    python3 heartbeat.py --report
    python3 heartbeat.py --selftest
"""

from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from time import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from homelab_secrets import get as _secret  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent

HEARTBEAT_DIR = Path(
    os.environ.get("HOMELAB_HEARTBEATS", _REPO_ROOT / "data" / "heartbeats")
)
MANIFEST = "manifest.json"

# Uptime Kuma por IP y no por nombre: el heartbeat no debe depender del DNS
# interno, que es justamente uno de los servicios monitorizados. Si el DNS cae,
# el latido tiene que seguir llegando.
KUMA_DEFAULT_BASE = os.environ.get("KUMA_BASE_URL", "http://localhost:3001")
KUMA_TIMEOUT_S = 5

# Tareas esperadas y su antigüedad máxima tolerable. El margen es holgado a
# propósito: un job semanal que se salta una ejecución por un reinicio no debe
# alertar, pero dos semanas de silencio sí.
DEFAULT_MANIFEST: dict[str, dict] = {
    "metrics-retention": {
        "label": "Retención métricas",
        "max_age_hours": 192,  # semanal + 1 día de margen
        "push": True,          # también notifica a Uptime Kuma
    },
}


def _token_key(job: str) -> str:
    """metrics-retention → KUMA_PUSH_METRICS_RETENTION"""
    return "KUMA_PUSH_" + job.upper().replace("-", "_").replace(".", "_")


def _log(msg: str) -> None:
    print(f"  heartbeat: {msg}", file=sys.stderr)


def _manifest_path() -> Path:
    return HEARTBEAT_DIR / MANIFEST


def load_manifest() -> dict[str, dict]:
    """
    Lee el manifiesto y lo completa con los valores por defecto que falten.

    Por qué la fusión: un manifiesto creado con una versión anterior del
    script no conocía claves añadidas después (`push`, por ejemplo) y las
    ignoraba en silencio. Devolver el fichero tal cual convertía cualquier
    opción nueva en código muerto para las instalaciones ya existentes.

    Solo se rellenan claves ausentes: lo que hayas cambiado a mano se respeta.
    Si hubo que completar algo, se reescribe el fichero.
    """
    path = _manifest_path()
    try:
        actual: dict[str, dict] = json.loads(path.read_text()) if path.exists() else {}
        if not isinstance(actual, dict):
            raise ValueError("el manifiesto no es un objeto JSON")
    except Exception as e:
        if path.exists():
            _log(f"manifiesto ilegible, se usan valores por defecto: {e}")
        actual = {}

    cambiado = False
    for job, defaults in DEFAULT_MANIFEST.items():
        entrada = actual.get(job)
        if not isinstance(entrada, dict):
            actual[job] = dict(defaults)
            cambiado = True
            continue
        for clave, valor in defaults.items():
            if clave not in entrada:
                entrada[clave] = valor
                cambiado = True

    if cambiado:
        try:
            HEARTBEAT_DIR.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(actual, indent=2, ensure_ascii=False))
        except Exception as e:
            _log(f"no se pudo actualizar el manifiesto: {e}")

    return actual


def push_kuma(job: str, status: str = "ok", detail: str = "") -> bool | None:
    """
    Notifica el latido a un monitor Push de Uptime Kuma.

    Reparto de responsabilidades: Kuma aporta alerta inmediata, historial y
    panel, y observa desde otra máquina. El fichero local aporta el detalle
    del error y sobrevive a que Kuma, la red o el DNS estén caídos. Por eso
    se hacen las dos cosas y no se elige una.

    Devuelve True (enviado), False (fallo) o None (no configurado).
    Nunca lanza: el latido local ya se ha escrito antes de llegar aquí.
    """
    token = _secret(_token_key(job))
    if not token:
        return None

    base = _secret("KUMA_BASE_URL") or KUMA_DEFAULT_BASE
    params = urllib.parse.urlencode(
        {"status": "up" if status == "ok" else "down", "msg": (detail or status)[:200]}
    )
    url = f"{base.rstrip('/')}/api/push/{token}?{params}"

    try:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        with urllib.request.urlopen(url, context=ctx, timeout=KUMA_TIMEOUT_S) as r:
            return r.status == 200
    except Exception as e:
        _log(f"push_kuma({job}) falló: {e}")
        return False


def write(job: str, status: str = "ok", detail: str = "", push: bool | None = None) -> bool:
    """
    Registra un latido local y, si procede, lo empuja a Uptime Kuma.

    El valor devuelto refleja **solo** la escritura local: es la que no puede
    fallar. Un push fallido se registra en stderr pero no invalida el latido,
    porque el informe diario se apoya en el fichero, no en Kuma.

    Nunca lanza: un fallo aquí no debe tumbar la tarea que lo llama.
    """
    ok_local = False
    try:
        HEARTBEAT_DIR.mkdir(parents=True, exist_ok=True)
        (HEARTBEAT_DIR / f"{job}.json").write_text(
            json.dumps(
                {
                    "job": job,
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                    "epoch": int(time()),
                    "status": status,
                    "detail": detail[:500],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        ok_local = True
    except Exception as e:
        _log(f"write({job}) falló: {e}")

    if push is None:
        push = bool(load_manifest().get(job, {}).get("push"))
    if push:
        push_kuma(job, status, detail)

    return ok_local


def read(job: str) -> dict | None:
    try:
        return json.loads((HEARTBEAT_DIR / f"{job}.json").read_text())
    except Exception:
        return None


def report() -> list[tuple[str, bool, float | None, str, str]]:
    """
    Estado de todas las tareas del manifiesto.

    Devuelve [(job, ok, edad_horas, status, detalle)].
    `ok` es False si nunca latió, si el latido está rancio, o si el último
    latido registró un error.
    """
    out = []
    ahora = time()
    for job, cfg in sorted(load_manifest().items()):
        hb = read(job)
        max_age = cfg.get("max_age_hours", 192)

        if hb is None:
            out.append((job, False, None, "ausente", "nunca ha latido"))
            continue

        edad = round((ahora - hb.get("epoch", 0)) / 3600, 1)
        status = hb.get("status", "ok")
        fresco = edad <= max_age
        ok = fresco and status == "ok"

        if not fresco:
            detalle = f"último latido hace {edad} h (máx {max_age} h)"
        elif status != "ok":
            detalle = hb.get("detail") or f"último estado: {status}"
        else:
            detalle = f"hace {edad} h"

        out.append((job, ok, edad, status, detalle))
    return out


def summary_line() -> str:
    """Línea compacta para el informe diario de Telegram."""
    filas = report()
    if not filas:
        return "💓 Latidos: sin tareas registradas"
    malas = [f for f in filas if not f[1]]
    if not malas:
        return f"💓 Latidos: ✅ {len(filas)}/{len(filas)}"
    detalle = " · ".join(f"{j}({d})" for j, _, _, _, d in malas[:3])
    return f"💓 Latidos: ❌ {len(filas) - len(malas)}/{len(filas)} · {detalle}"


# ── Autocomprobación ──────────────────────────────────────────────────────────


def _selftest() -> int:
    import tempfile

    global HEARTBEAT_DIR
    original = HEARTBEAT_DIR
    HEARTBEAT_DIR = Path(tempfile.mkdtemp()) / "hb"
    fallos = []

    def check(label, cond):
        print(f"  {'OK  ' if cond else 'FALLO'} {label}")
        if not cond:
            fallos.append(label)

    try:
        m = load_manifest()
        check("crea el manifiesto si falta", _manifest_path().exists())
        check("manifiesto con la tarea por defecto", "metrics-retention" in m)

        # Regresión: un manifiesto de una versión anterior debe heredar las
        # claves nuevas. Antes se devolvía tal cual y `push` quedaba muerta.
        _manifest_path().write_text(json.dumps(
            {"metrics-retention": {"label": "Antiguo", "max_age_hours": 999}}))
        m = load_manifest()
        check("hereda claves nuevas", m["metrics-retention"].get("push") is True)
        check("respeta los valores ya presentes",
              m["metrics-retention"]["max_age_hours"] == 999
              and m["metrics-retention"]["label"] == "Antiguo")
        check("la fusión se persiste en disco",
              json.loads(_manifest_path().read_text())["metrics-retention"].get("push") is True)

        _manifest_path().write_text("{ esto no es json")
        check("manifiesto corrupto no rompe", "metrics-retention" in load_manifest())

        r = report()
        check("tarea sin latido se marca mal", r[0][1] is False and r[0][3] == "ausente")

        check("write devuelve True", write("metrics-retention") is True)
        r = report()
        check("tras latir, correcta", r[0][1] is True)
        check("edad cercana a cero", r[0][2] is not None and r[0][2] < 0.1)
        check("línea resumen en verde", "✅" in summary_line())

        check("write con error", write("metrics-retention", "error", "exit 1") is True)
        r = report()
        check("estado error marca fallo", r[0][1] is False)
        check("el detalle se conserva", "exit 1" in r[0][4])
        check("línea resumen en rojo", "❌" in summary_line())

        # Latido rancio: se reescribe con marca de tiempo antigua.
        (HEARTBEAT_DIR / "metrics-retention.json").write_text(
            json.dumps({"job": "metrics-retention", "epoch": int(time() - 400 * 3600),
                        "status": "ok", "detail": ""})
        )
        r = report()
        check("latido rancio marca fallo", r[0][1] is False)
        check("el motivo menciona la antigüedad", "máx" in r[0][4])

        # ── Push a Uptime Kuma ─────────────────────────────────────────────
        check("sin token configurado, push devuelve None",
              push_kuma("tarea-inexistente") is None)
        check("nombre de clave del token",
              _token_key("metrics-retention") == "KUMA_PUSH_METRICS_RETENTION")
        check("nombre de clave con puntos",
              _token_key("gbrain.reindex") == "KUMA_PUSH_GBRAIN_REINDEX")

        # Con token pero host inalcanzable: debe fallar sin lanzar y sin
        # invalidar el latido local, que es lo que sostiene el informe diario.
        os.environ["KUMA_PUSH_ROTA"] = "tokenfalso"
        os.environ["KUMA_BASE_URL"] = "http://127.0.0.1:1"  # puerto cerrado
        try:
            check("push a host caído devuelve False", push_kuma("rota") is False)
            check("el latido local sobrevive a un push fallido",
                  write("rota", push=True) is True)
            check("y queda registrado en disco", read("rota") is not None)
        finally:
            os.environ.pop("KUMA_PUSH_ROTA", None)
            os.environ.pop("KUMA_BASE_URL", None)

        HEARTBEAT_DIR = Path("/no/se/puede/crear")
        check("no lanza con ruta inválida", write("x", push=False) is False)
        check("read devuelve None si no hay fichero", read("x") is None)
    finally:
        HEARTBEAT_DIR = original

    print()
    if fallos:
        print(f"FALLOS: {len(fallos)} → {fallos}")
        return 1
    print("Todo correcto.")
    return 0


def _cli() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Latidos de tareas programadas.")
    ap.add_argument("--write", metavar="JOB", help="registra un latido")
    ap.add_argument("--status", default="ok", help="ok | error (por defecto ok)")
    ap.add_argument("--detail", default="", help="texto libre, p.ej. el error")
    ap.add_argument("--report", action="store_true", help="estado de todas las tareas")
    ap.add_argument("--no-push", action="store_true", help="con --write: no notifica a Kuma")
    ap.add_argument("--test-push", metavar="JOB", help="solo el push a Kuma, sin escribir")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return _selftest()

    if args.test_push:
        r = push_kuma(args.test_push, args.status, args.detail or "prueba manual")
        if r is None:
            print(f"{args.test_push}: sin token — falta {_token_key(args.test_push)} "
                  f"en el fichero de secretos")
            return 1
        print(f"{args.test_push}: push {'enviado' if r else 'FALLIDO'} "
              f"({_secret('KUMA_BASE_URL') or KUMA_DEFAULT_BASE})")
        return 0 if r else 1

    if args.write:
        push = False if args.no_push else None
        ok = write(args.write, args.status, args.detail, push=push)
        estado_push = load_manifest().get(args.write, {}).get("push") and not args.no_push
        print(f"latido {'registrado' if ok else 'NO registrado'}: {args.write} "
              f"({args.status}){' · push a Kuma' if estado_push else ''}")
        return 0 if ok else 1

    for job, ok, edad, status, detalle in report():
        marca = "OK  " if ok else "FALLO"
        print(f"  {marca} {job:22} {detalle}")
    print()
    print(summary_line())
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
