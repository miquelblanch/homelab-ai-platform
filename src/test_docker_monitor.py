#!/usr/bin/env python3
"""
test_docker_monitor.py — Pruebas de la lógica pura de docker_monitor.

Sin dependencias externas ni Docker: solo se ejercitan las funciones que
no tocan el sistema (parseo y decisión del cortacircuitos), más la
integración de recent_restarts contra una BD temporal.

    python3 test_docker_monitor.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from time import time

sys.path.insert(0, str(Path(__file__).resolve().parent))

import docker_monitor as dm
import metrics_db

FAILURES: list[str] = []


def check(label: str, cond: bool) -> None:
    print(f"  {'OK  ' if cond else 'FALLO'} {label}")
    if not cond:
        FAILURES.append(label)


def test_health_from_status() -> None:
    print("health_from_status")
    casos = [
        ("Up 2 weeks (healthy)", "healthy"),
        ("Up 3 hours (unhealthy)", "unhealthy"),
        ("Up 5 seconds (health: starting)", "starting"),
        ("Up 10 hours", ""),
        ("Exited (1) 3 minutes ago", ""),
        ("Restarting (1) 2 seconds ago", ""),
        ("", ""),
    ]
    for entrada, esperado in casos:
        check(f"{entrada!r} → {esperado!r}", dm.health_from_status(entrada) == esperado)

    # 'unhealthy' contiene 'healthy' como subcadena: el orden de comprobación importa.
    check("unhealthy no se confunde con healthy",
          dm.health_from_status("Up 1 hour (unhealthy)") == "unhealthy")


def test_parse_ps() -> None:
    print("\nparse_ps")
    out = (
        "traefik\tUp 2 hours\trunning\ttraefik:v3\n"
        "metube\tExited (1) 3 min ago\texited\talexta69/metube\n"
        "foo\tUp 1 hour (unhealthy)\trunning\tfoo:1\n"
        "sin_imagen\tUp 1 hour\trunning\n"
        "linea_corta\tUp\n"
        "\n"
    )
    rows = dm.parse_ps(out)
    check("descarta líneas incompletas", len(rows) == 4)
    check("running correcto", [r["running"] for r in rows] == [True, False, True, True])
    check("imagen ausente → None", rows[3]["image"] is None)
    check("health separado de healthy",
          rows[2]["health"] == "unhealthy" and rows[2]["healthy"] is False)
    check("sin healthcheck → health vacío pero healthy True",
          rows[0]["health"] == "" and rows[0]["healthy"] is True)


def test_parse_stats() -> None:
    print("\nparse_stats")
    out = (
        "traefik\t1.24%\t48.2MiB / 62.79GiB\t0.07%\n"
        "immich\t112.50%\t1.234GiB / 62.79GiB\t1.97%\n"
        "raro\t--\tn/a\t--\n"
    )
    s = dm.parse_stats(out)
    check("cpu por encima de 100%", s["immich"]["cpu_percent"] == 112.5)
    check("GiB a MB", s["immich"]["memory_mb"] == 1263.6)
    check("MiB a MB", s["traefik"]["memory_mb"] == 48.2)
    check("valores no numéricos → None", s["raro"]["cpu_percent"] is None)


def test_breaker_decision() -> None:
    print("\nbreaker_decision (máx 3)")
    check("0 intentos: permite", dm.breaker_decision(0, 3)[0] is True)
    check("2 intentos: permite", dm.breaker_decision(2, 3)[0] is True)
    check("3 intentos: bloquea", dm.breaker_decision(3, 3)[0] is False)
    check("9 intentos: bloquea", dm.breaker_decision(9, 3)[0] is False)
    check("el motivo explica el bloqueo",
          "cortacircuitos" in dm.breaker_decision(3, 3)[1])


def test_recent_restarts() -> None:
    print("\nrecent_restarts (BD temporal)")
    original = metrics_db.DB_PATH
    metrics_db.DB_PATH = Path(tempfile.mkdtemp()) / "t.db"
    try:
        metrics_db.init_db()

        with metrics_db._conn() as conn:
            ahora = int(time())
            filas = [
                ("beszel", ahora - 600, "success"),      # dentro de ventana
                ("beszel", ahora - 1200, "failed"),      # dentro de ventana
                ("beszel", ahora - 40 * 3600, "success"),  # fuera de ventana
                ("beszel", ahora - 300, "circuit_breaker"),  # no cuenta
                ("beszel", ahora - 300, "skipped"),      # no cuenta
                ("otro", ahora - 300, "success"),        # otro contenedor
            ]
            conn.executemany(
                "INSERT INTO restart_history (container_name, timestamp, result) VALUES (?,?,?)",
                filas,
            )

        n = metrics_db.recent_restarts("beszel", 6)
        check("cuenta solo intentos reales en ventana", n == 2)
        check("ventana amplia incluye el antiguo", metrics_db.recent_restarts("beszel", 72) == 3)
        check("aísla por contenedor", metrics_db.recent_restarts("otro", 6) == 1)
        check("contenedor sin historial → 0", metrics_db.recent_restarts("nuevo", 6) == 0)

        # El escenario real del 2026-04-18: tres intentos, el cuarto se bloquea.
        permitir, _ = dm.breaker_decision(metrics_db.recent_restarts("beszel", 6))
        check("con 2 intentos aún permite", permitir is True)
        with metrics_db._conn() as conn:
            conn.execute(
                "INSERT INTO restart_history (container_name, timestamp, result) VALUES (?,?,?)",
                ("beszel", int(time()) - 60, "failed"),
            )
        permitir, motivo = dm.breaker_decision(metrics_db.recent_restarts("beszel", 6))
        check("con 3 intentos bloquea", permitir is False)

        metrics_db.DB_PATH = Path("/no/existe/x.db")
        check("fallo de BD no bloquea el reinicio", metrics_db.recent_restarts("beszel", 6) == 0)
    finally:
        metrics_db.DB_PATH = original


def main() -> int:
    for fn in (test_health_from_status, test_parse_ps, test_parse_stats,
               test_breaker_decision, test_recent_restarts):
        fn()
    print()
    if FAILURES:
        print(f"FALLOS: {len(FAILURES)} → {FAILURES}")
        return 1
    print("Todo correcto.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
