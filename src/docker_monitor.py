#!/usr/bin/env python3
"""
docker_monitor.py — Monitor autónomo de contenedores Docker.
Sin dependencias externas. Mismo patrón que ha_monitor.py.

- Detecta contenedores caídos o unhealthy
- Auto-reinicia los no críticos
- Incluye últimas líneas de log en la alerta Telegram
- Respeta ventana de mantenimiento (mwin)
- Comprueba uso de disco
- Solo alerta en cambio de estado
- Persiste métricas y eventos de remediación en SQLite (metrics_db)

Flags:
    --dry-run   No reinicia nada, no manda Telegram, no escribe estado.
                Sí escribe métricas. Útil para verificar tras un cambio.
"""

import json
import os
import ssl
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from time import sleep, time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import metrics_db  # noqa: E402
from homelab_secrets import telegram  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent
DOCKER_BIN = os.environ.get("DOCKER_BIN", "docker")

# ── Configuración ─────────────────────────────────────────────────────────────

BOT_TOKEN, CHAT_ID = telegram()

DRY_RUN = "--dry-run" in sys.argv

STATE_FILE = Path(os.environ.get("HOMELAB_STATE", _REPO_ROOT / "data" / "docker_monitor_state.json"))
MAINT_FILE = Path(os.environ.get("HOMELAB_MAINT_FILE", _REPO_ROOT / "data" / ".maintenance_until"))

# Contenedores que NUNCA se restartan (apagados intencionalmente o críticos)
NEVER_RESTART = {
    "frigate",
}

# Contenedores críticos: solo alerta, no se tocan automáticamente
CRITICAL = {
    "homeassistant",
    "vaultwarden",
    "nextcloud",
    "nextcloud-db",
    "nextcloud_redis",
    "immich_server",
    "immich_machine_learning",
    "immich_postgres",
    "immich_redis",
    "pangolin-server",
    "gerbil",
    "traefik",
}

# Umbrales de disco
DISK_CHECKS = [
    {"path": os.environ.get("DISK_DATA", "/data"),   "label": "Data",    "threshold": 90},
    {"path": os.environ.get("DISK_BACKUP", "/backup"), "label": "Backup",  "threshold": 90},
    {"path": "/",                                     "label": "System",  "threshold": 85},
]

LOG_LINES = 8  # líneas de log a incluir en la alerta

# ── Remediación ───────────────────────────────────────────────────────────────
# Un `docker restart` que devuelve 0 NO significa que el servicio se haya
# recuperado. Tras reiniciar se espera y se comprueba el estado real.
VERIFY_DELAY_S = 10

# Cortacircuitos: si un contenedor acumula CB_MAX_ATTEMPTS intentos en las
# últimas CB_WINDOW_HOURS, se deja de reiniciar y se escala a Telegram.
# Calibrado sobre el episodio del 2026-04-18 (05:50, 05:55, 06:02, 06:55...):
# con estos valores el cuarto intento se habría bloqueado.
CB_WINDOW_HOURS = 6
CB_MAX_ATTEMPTS = 3


# ── Utilidades ────────────────────────────────────────────────────────────────

def in_maintenance() -> bool:
    try:
        if MAINT_FILE.exists():
            until = float(MAINT_FILE.read_text().strip())
            return until > time()
    except Exception:
        pass
    return False


def send_telegram(message: str) -> bool:
    if DRY_RUN:
        print(f"  [dry-run] Telegram:\n{message}")
        return True
    if not BOT_TOKEN or not CHAT_ID:
        print("  Telegram error: faltan TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID", file=sys.stderr)
        return False
    url  = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = json.dumps({"chat_id": CHAT_ID, "text": message}).encode()
    ctx  = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode    = ssl.CERT_NONE
    try:
        req = urllib.request.Request(url, data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, context=ctx, timeout=10) as r:
            return json.loads(r.read()).get("ok", False)
    except Exception as e:
        print(f"  Telegram error: {e}", file=sys.stderr)
        return False


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def save_state(state: dict):
    if DRY_RUN:
        print("  [dry-run] estado no guardado")
        return
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def get_containers() -> list[dict]:
    """Devuelve lista de contenedores con name, image, status, running, healthy."""
    try:
        out = subprocess.check_output(
            [DOCKER_BIN, "ps", "-a",
             "--format", "{{.Names}}\t{{.Status}}\t{{.State}}\t{{.Image}}"],
            timeout=15
        ).decode()
    except Exception as e:
        print(f"  docker ps error: {e}", file=sys.stderr)
        return []

    return parse_ps(out)


def health_from_status(status: str) -> str:
    """
    Extrae el campo health de Docker a partir del texto de estado.

    Devuelve 'healthy' | 'unhealthy' | 'starting' | '' (sin healthcheck).

    Importante: cadena vacía y 'healthy' NO son lo mismo. Un contenedor sin
    HEALTHCHECK definido no está sano, está sin comprobar. Confundirlos
    rompe la comparabilidad con el histórico de abril-mayo, que guardaba
    el campo real de Docker.
    """
    s = status.lower()
    if "(unhealthy)" in s:
        return "unhealthy"
    if "(healthy)" in s:
        return "healthy"
    if "health: starting" in s:
        return "starting"
    return ""


def parse_ps(out: str) -> list[dict]:
    """Parsea la salida de `docker ps` (extraído para poder testearlo)."""
    containers = []
    for line in out.strip().splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        name, status, state = parts[0], parts[1], parts[2]
        image = parts[3] if len(parts) > 3 else None
        containers.append({
            "name":    name,
            "image":   image,
            "status":  status,
            "running": state == "running",
            # Para la lógica de alerta: solo 'unhealthy' explícito cuenta como fallo.
            "healthy": "unhealthy" not in status.lower(),
            # Para las métricas: el campo real de Docker.
            "health":  health_from_status(status),
        })
    return containers


def _parse_pct(raw: str) -> float | None:
    try:
        return float(raw.strip().rstrip("%"))
    except Exception:
        return None


_MEM_UNITS = {"b": 1 / 1024**2, "kib": 1 / 1024, "kb": 1 / 1000,
              "mib": 1.0, "mb": 1.0, "gib": 1024.0, "gb": 1000.0,
              "tib": 1024.0**2, "tb": 1000.0**2}


def _parse_mem_mb(raw: str) -> float | None:
    """'1.234GiB / 62.79GiB' → 1263.6 (MB)."""
    try:
        used = raw.split("/")[0].strip().lower()
        num = ""
        for ch in used:
            if ch.isdigit() or ch == ".":
                num += ch
            else:
                break
        unit = used[len(num):].strip()
        return round(float(num) * _MEM_UNITS.get(unit, 1.0), 1)
    except Exception:
        return None


def parse_stats(out: str) -> dict[str, dict]:
    """Parsea la salida de `docker stats` (extraído para poder testearlo)."""
    stats = {}
    for line in out.strip().splitlines():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        name, cpu, mem, mem_pct = parts[0], parts[1], parts[2], parts[3]
        stats[name] = {
            "cpu_percent":    _parse_pct(cpu),
            "memory_mb":      _parse_mem_mb(mem),
            "memory_percent": _parse_pct(mem_pct),
        }
    return stats


def get_container_stats() -> dict[str, dict]:
    """CPU y memoria por contenedor. Si falla, se registran las métricas sin ellos."""
    try:
        out = subprocess.check_output(
            [DOCKER_BIN, "stats", "--no-stream",
             "--format", "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}"],
            timeout=60
        ).decode()
        return parse_stats(out)
    except Exception as e:
        print(f"  docker stats error: {e}", file=sys.stderr)
        return {}


def get_container_logs(name: str, lines: int = LOG_LINES) -> str:
    try:
        out = subprocess.check_output(
            [DOCKER_BIN, "logs", "--tail", str(lines), name],
            stderr=subprocess.STDOUT,
            timeout=10
        ).decode(errors="replace")
        return out.strip() or "(sin logs)"
    except Exception:
        return "(logs no disponibles)"


def container_is_running(name: str) -> bool:
    """Estado real según `docker inspect`, no según el código de salida del restart."""
    try:
        out = subprocess.check_output(
            [DOCKER_BIN, "inspect", "-f", "{{.State.Running}}", name],
            stderr=subprocess.DEVNULL,
            timeout=10,
        ).decode().strip()
        return out == "true"
    except Exception:
        return False


def breaker_decision(attempts: int, max_attempts: int = CB_MAX_ATTEMPTS) -> tuple[bool, str]:
    """
    Decide si procede reiniciar. Función pura, para poder testearla.

    Devuelve (permitir, motivo).
    """
    if attempts >= max_attempts:
        return False, (
            f"cortacircuitos abierto: {attempts} intentos en las últimas "
            f"{CB_WINDOW_HOURS} h (máximo {max_attempts})"
        )
    return True, f"intento {attempts + 1}/{max_attempts} en {CB_WINDOW_HOURS} h"


def restart_container(name: str, reason: str = "") -> bool:
    """
    Reinicia y **verifica**. Devuelve True solo si el contenedor está
    realmente corriendo después.

    El motivo de esta función: hasta el 2026-07-26 se registraba 'success'
    cuando `docker restart` devolvía 0. El análisis del histórico mostró
    contenedores marcados como recuperados que estuvieron horas a cero
    memoria. La métrica medía el código de salida, no el resultado.
    """
    if DRY_RUN:
        print(f"  [dry-run] docker restart {name} ({reason})")
        return True

    try:
        subprocess.check_call(
            [DOCKER_BIN, "restart", name],
            stdout=subprocess.DEVNULL,
            timeout=30,
        )
    except Exception as e:
        print(f"  docker restart {name} error: {e}", file=sys.stderr)
        metrics_db.record_restart(name, "failed", f"{reason} | comando: {e}"[:500])
        return False

    sleep(VERIFY_DELAY_S)
    running = container_is_running(name)

    if running:
        metrics_db.record_restart(name, "success", f"{reason} | verificado tras {VERIFY_DELAY_S}s")
    else:
        metrics_db.record_restart(
            name, "failed",
            f"{reason} | comando OK pero no está running tras {VERIFY_DELAY_S}s",
        )
        print(f"  {name}: restart devolvió 0 pero el contenedor NO está corriendo", file=sys.stderr)
    return running


def check_disk(path: str) -> tuple[float, float]:
    """Devuelve (usado_pct, libre_gb)."""
    try:
        import shutil
        total, used, free = shutil.disk_usage(path)
        pct  = used / total * 100
        free_gb = free / (1024 ** 3)
        return round(pct, 1), round(free_gb, 1)
    except Exception:
        return -1, -1


# ── Lógica principal ──────────────────────────────────────────────────────────

def main():
    now   = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    maint = in_maintenance()
    state = load_state()
    new_state = dict(state)  # preserva entradas no vistas hoy

    metrics_db.init_db()

    # ── Contenedores ──────────────────────────────────────────────────────────
    containers = get_containers()

    # Métricas: una muestra por contenedor y ciclo. Se escriben siempre,
    # también en dry-run y en ventana de mantenimiento — un incidente
    # silenciado sigue siendo un dato.
    stats = get_container_stats()
    n = metrics_db.record_containers(
        [
            {
                "name":   c["name"],
                "image":  c.get("image"),
                "status": c["status"],
                "health": c.get("health", ""),
                **stats.get(c["name"], {}),
            }
            for c in containers
        ],
        timestamp=now,
    )
    print(f"[{now}] métricas registradas: {n} contenedores")

    for c in containers:
        name    = c["name"]
        ok      = c["running"] and c["healthy"]
        status  = c["status"]

        if name in NEVER_RESTART:
            print(f"[{now}] {name} SKIP (never-restart, state={status})")
            continue

        prev     = state.get(f"container:{name}", {})
        was_ok   = prev.get("ok", True)
        dn_since = prev.get("down_since")

        if ok:
            if not was_ok and not maint:
                send_telegram(
                    f"✅ docker-monitor — {name} recuperado\n"
                    f"🕐 {now}\n"
                    f"Caído desde: {dn_since or '?'}\n"
                    f"Estado: {status}"
                )
                print(f"[{now}] {name} RECOVERED")
            else:
                print(f"[{now}] {name} OK ({status})")
            new_state[f"container:{name}"] = {"ok": True, "down_since": None}

        else:
            if was_ok and not maint:
                is_critical = name in CRITICAL

                if is_critical:
                    send_telegram(
                        f"🚨 docker-monitor — {name} CAÍDO [CRÍTICO]\n"
                        f"🕐 {now}\n"
                        f"Estado: {status}\n"
                        f"⚠️ No se reinicia automáticamente"
                    )
                    metrics_db.record_restart(
                        name, "skipped", f"crítico, escalado a humano | {status}"
                    )
                    print(f"[{now}] {name} ALERT crítico — notificado")
                else:
                    intentos = metrics_db.recent_restarts(name, CB_WINDOW_HOURS)
                    permitir, motivo = breaker_decision(intentos)

                    if not permitir:
                        metrics_db.record_restart(name, "circuit_breaker", motivo)
                        send_telegram(
                            f"🔴 docker-monitor — {name} en bucle de reinicios\n"
                            f"🕐 {now}\n"
                            f"Estado: {status}\n"
                            f"{motivo}\n"
                            f"⛔ Reinicio automático detenido — requiere intervención\n\n"
                            f"{get_container_logs(name)}"
                        )
                        print(f"[{now}] {name} CIRCUIT BREAKER — {motivo}")
                    else:
                        restarted = restart_container(name, reason=f"{status} | {motivo}")
                        restart_txt = (
                            "✅ Reiniciado y verificado" if restarted
                            else "❌ Reinicio sin efecto — sigue caído"
                        )
                        send_telegram(
                            f"⚠️ docker-monitor — {name} CAÍDO\n"
                            f"🕐 {now}\n"
                            f"Estado: {status}\n"
                            f"{restart_txt}  ({motivo})"
                        )
                        print(f"[{now}] {name} ALERT — {restart_txt}")
            else:
                print(f"[{now}] {name} ALERT — ya notificado ({status})")

            new_state[f"container:{name}"] = {"ok": False, "down_since": dn_since or now}

    # ── Discos ────────────────────────────────────────────────────────────────
    disk_samples = []

    for disk in DISK_CHECKS:
        path      = disk["path"]
        label     = disk["label"]
        threshold = disk["threshold"]
        did       = f"disk:{path}"

        pct, free_gb = check_disk(path)
        if pct < 0:
            print(f"[{now}] disco {label} — no disponible")
            continue

        disk_samples.append({
            "path": path, "label": label,
            "used_percent": pct, "free_gb": free_gb,
        })

        ok       = pct < threshold
        prev     = state.get(did, {})
        was_ok   = prev.get("ok", True)
        dn_since = prev.get("down_since")

        if ok:
            if not was_ok and not maint:
                send_telegram(
                    f"✅ docker-monitor — disco {label} recuperado\n"
                    f"🕐 {now}\n"
                    f"Uso actual: {pct}% · Libre: {free_gb} GB"
                )
                print(f"[{now}] disco {label} OK ({pct}%)")
            else:
                print(f"[{now}] disco {label} OK ({pct}% usado, {free_gb} GB libres)")
            new_state[did] = {"ok": True, "down_since": None}
        else:
            if was_ok and not maint:
                send_telegram(
                    f"🚨 docker-monitor — disco {label} lleno\n"
                    f"🕐 {now}\n"
                    f"Uso: {pct}% (umbral {threshold}%) · Libre: {free_gb} GB"
                )
                print(f"[{now}] disco {label} ALERT — {pct}% usado")
            else:
                print(f"[{now}] disco {label} ALERT — ya notificado ({pct}%)")
            new_state[did] = {"ok": False, "down_since": dn_since or now}

    metrics_db.record_disks(disk_samples, timestamp=now)

    save_state(new_state)


if __name__ == "__main__":
    main()
