#!/usr/bin/env python3
"""
metrics_db.py — Persistencia de métricas del homelab en SQLite.

Diseño:
- Sin dependencias externas (solo stdlib), igual que el resto de monitores.
- **A prueba de fallos**: ninguna función lanza excepción hacia el llamante.
  Un error de escritura nunca debe tumbar un monitor que corre cada 5 min.
- Esquema compatible con las tablas históricas de homelab.db (abril-mayo 2026),
  para que la serie temporal sea continua en lugar de empezar de cero.

Uso:
    import metrics_db
    metrics_db.init_db()
    metrics_db.record_containers([{...}, ...])
    metrics_db.record_restart("metube", "success", reason="unhealthy")
    metrics_db.record_disks([{...}, ...])

Autocomprobación:
    python3 metrics_db.py --selftest
"""

from __future__ import annotations

import os
import sqlite3
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from time import time

_REPO_ROOT = Path(__file__).resolve().parent

# ── Configuración ─────────────────────────────────────────────────────────────

DB_PATH = Path(
    os.environ.get("HOMELAB_DB", _REPO_ROOT / "data" / "homelab.db")
)

# Días de detalle a 5 min que se conservan sin comprimir (ver retention()).
RAW_KEEP_DAYS = 30

# Alias histórico, mantenido por compatibilidad con llamadas antiguas a prune().
DEFAULT_KEEP_DAYS = RAW_KEEP_DAYS

# `container_metrics` y `restart_history` ya existen en la BD histórica con
# este mismo esquema; los CREATE son no-ops sobre ellas. `disk_metrics` es nueva.
SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS container_metrics (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp      TEXT NOT NULL,
        container      TEXT NOT NULL,
        image          TEXT,
        status         TEXT,
        health         TEXT,
        cpu_percent    REAL,
        memory_mb      REAL,
        memory_percent REAL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS restart_history (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        container_name TEXT    NOT NULL,
        timestamp      INTEGER NOT NULL,
        result         TEXT    NOT NULL,
        reason         TEXT,
        triggered_by   TEXT    DEFAULT 'auto'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS disk_metrics (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp    TEXT NOT NULL,
        path         TEXT NOT NULL,
        label        TEXT,
        used_percent REAL,
        free_gb      REAL
    )
    """,
    # ── Agregados de retención larga ─────────────────────────────────────────
    # El detalle de 5 min se conserva RAW_KEEP_DAYS; más allá se comprime a
    # media horaria (contenedores) y diaria (discos), que se guarda siempre.
    """
    CREATE TABLE IF NOT EXISTS container_metrics_hourly (
        hour          TEXT NOT NULL,   -- 'YYYY-MM-DDTHH'
        container     TEXT NOT NULL,
        samples       INTEGER NOT NULL,
        cpu_avg       REAL,
        cpu_max       REAL,
        memory_avg_mb REAL,
        memory_max_mb REAL,
        healthy_ratio REAL,            -- fracción de muestras sin 'unhealthy'
        PRIMARY KEY (hour, container)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS disk_metrics_daily (
        day              TEXT NOT NULL,  -- 'YYYY-MM-DD'
        path             TEXT NOT NULL,
        label            TEXT,
        samples          INTEGER NOT NULL,
        used_percent_avg REAL,
        used_percent_max REAL,
        free_gb_min      REAL,
        PRIMARY KEY (day, path)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_cm_ts           ON container_metrics(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_cm_container_ts ON container_metrics(container, timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_rh_ts           ON restart_history(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_dm_ts           ON disk_metrics(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_cmh_hour        ON container_metrics_hourly(hour)",
]

# Valores permitidos en restart_history.result
RESULT_VALUES = {"success", "failed", "circuit_breaker", "skipped"}


# ── Infraestructura ───────────────────────────────────────────────────────────


def _log(msg: str) -> None:
    print(f"  metrics_db: {msg}", file=sys.stderr)


@contextmanager
def _conn():
    """Conexión con WAL y timeout, siempre cerrada."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=15000")
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> bool:
    """Crea tablas e índices si faltan. Idempotente."""
    try:
        with _conn() as conn:
            for stmt in SCHEMA:
                conn.execute(stmt)
        return True
    except Exception as e:
        _log(f"init_db falló: {e}")
        return False


# ── Escritura ─────────────────────────────────────────────────────────────────


def record_containers(rows: list[dict], timestamp: str | None = None) -> int:
    """
    Registra una muestra por contenedor.

    Cada dict admite: name (obligatorio), image, status, health,
    cpu_percent, memory_mb, memory_percent.

    Devuelve el número de filas insertadas (0 si falla).
    """
    rows = [r for r in (rows or []) if r.get("name")]
    if not rows:
        return 0

    ts = timestamp or datetime.now().isoformat(timespec="seconds")
    try:
        with _conn() as conn:
            conn.executemany(
                "INSERT INTO container_metrics "
                "(timestamp, container, image, status, health, "
                " cpu_percent, memory_mb, memory_percent) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        ts,
                        r.get("name"),
                        r.get("image"),
                        r.get("status"),
                        r.get("health"),
                        r.get("cpu_percent"),
                        r.get("memory_mb"),
                        r.get("memory_percent"),
                    )
                    for r in rows
                ],
            )
        return len(rows)
    except Exception as e:
        _log(f"record_containers falló: {e}")
        return 0


def record_restart(
    container: str,
    result: str,
    reason: str | None = None,
    triggered_by: str = "auto",
) -> bool:
    """
    Registra un evento de remediación.

    result: 'success'   → reiniciado correctamente
            'failed'    → se intentó y falló
            'skipped'   → caído pero no se toca (crítico, escalado a humano)
            'circuit_breaker' → bloqueado por demasiados intentos
    """
    if result not in RESULT_VALUES:
        _log(f"result inválido '{result}', se registra igual")
    try:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO restart_history "
                "(container_name, timestamp, result, reason, triggered_by) "
                "VALUES (?, ?, ?, ?, ?)",
                (container, int(time()), result, reason, triggered_by),
            )
        return True
    except Exception as e:
        _log(f"record_restart falló: {e}")
        return False


def recent_restarts(
    container: str,
    hours: int = 6,
    results: tuple[str, ...] = ("success", "failed"),
) -> int:
    """
    Cuenta intentos de remediación de un contenedor en la ventana reciente.

    Base del cortacircuitos. Excluye 'circuit_breaker' y 'skipped' por defecto:
    son decisiones de no actuar, no intentos, y contarlas haría que el
    cortacircuitos se auto-alimentase y no se abriera nunca.

    Ante un fallo de lectura devuelve 0: preferimos intentar el reinicio a
    bloquearlo por un problema de la base de datos.
    """
    try:
        placeholders = ",".join("?" * len(results))
        with _conn() as conn:
            return conn.execute(
                f"SELECT COUNT(*) FROM restart_history "
                f"WHERE container_name = ? AND timestamp >= ? "
                f"AND result IN ({placeholders})",
                (container, int(time() - hours * 3600), *results),
            ).fetchone()[0]
    except Exception as e:
        _log(f"recent_restarts falló: {e}")
        return 0


def record_disks(rows: list[dict], timestamp: str | None = None) -> int:
    """
    Registra una muestra por disco.
    Cada dict admite: path (obligatorio), label, used_percent, free_gb.
    """
    rows = [r for r in (rows or []) if r.get("path")]
    if not rows:
        return 0

    ts = timestamp or datetime.now().isoformat(timespec="seconds")
    try:
        with _conn() as conn:
            conn.executemany(
                "INSERT INTO disk_metrics "
                "(timestamp, path, label, used_percent, free_gb) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        ts,
                        r.get("path"),
                        r.get("label"),
                        r.get("used_percent"),
                        r.get("free_gb"),
                    )
                    for r in rows
                ],
            )
        return len(rows)
    except Exception as e:
        _log(f"record_disks falló: {e}")
        return 0


# ── Mantenimiento ─────────────────────────────────────────────────────────────


def _cutoff_hour(keep_days: int) -> str:
    """Frontera de retención truncada a la hora: 'YYYY-MM-DDTHH'.

    Truncar es lo que hace segura la política: agregación y purga usan
    exactamente el mismo corte, así que una hora entra completa en el
    agregado o no entra. Nunca se borra detalle a medio comprimir.
    """
    return datetime.fromtimestamp(time() - keep_days * 86400).strftime("%Y-%m-%dT%H")


def rollup(keep_days: int = RAW_KEEP_DAYS) -> dict:
    """
    Comprime el detalle anterior al corte en medias horarias y diarias.

    Idempotente: recalcula desde el detalle y sustituye el agregado
    (INSERT OR REPLACE sobre la clave primaria), así que ejecutarlo dos
    veces da el mismo resultado.
    """
    cut_h = _cutoff_hour(keep_days)
    cut_d = cut_h[:10]
    out = {"container_metrics_hourly": 0, "disk_metrics_daily": 0}
    try:
        with _conn() as conn:
            cur = conn.execute(
                """
                INSERT OR REPLACE INTO container_metrics_hourly
                    (hour, container, samples, cpu_avg, cpu_max,
                     memory_avg_mb, memory_max_mb, healthy_ratio)
                SELECT substr(timestamp, 1, 13) AS hour,
                       container,
                       COUNT(*),
                       ROUND(AVG(cpu_percent), 2),
                       ROUND(MAX(cpu_percent), 2),
                       ROUND(AVG(memory_mb), 1),
                       ROUND(MAX(memory_mb), 1),
                       ROUND(AVG(CASE WHEN health = 'healthy' THEN 1.0 ELSE 0.0 END), 4)
                FROM container_metrics
                WHERE substr(timestamp, 1, 13) < ?
                GROUP BY hour, container
                """,
                (cut_h,),
            )
            out["container_metrics_hourly"] = cur.rowcount

            cur = conn.execute(
                """
                INSERT OR REPLACE INTO disk_metrics_daily
                    (day, path, label, samples,
                     used_percent_avg, used_percent_max, free_gb_min)
                SELECT substr(timestamp, 1, 10) AS day,
                       path,
                       MAX(label),
                       COUNT(*),
                       ROUND(AVG(used_percent), 2),
                       ROUND(MAX(used_percent), 2),
                       ROUND(MIN(free_gb), 1)
                FROM disk_metrics
                WHERE substr(timestamp, 1, 10) < ?
                GROUP BY day, path
                """,
                (cut_d,),
            )
            out["disk_metrics_daily"] = cur.rowcount
    except Exception as e:
        _log(f"rollup falló: {e}")
    return out


def prune(keep_days: int = RAW_KEEP_DAYS, require_rollup: bool = True) -> dict:
    """
    Borra el detalle a 5 min anterior al corte.

    Con require_rollup=True (por defecto) solo borra las horas que ya
    existen en el agregado. Es la red de seguridad: si el rollup falló,
    la purga no destruye datos irrecuperables.

    `restart_history` NO se purga nunca: son eventos, no muestras.
    """
    cut_h = _cutoff_hour(keep_days)
    cut_d = cut_h[:10]
    deleted = {"container_metrics": 0, "disk_metrics": 0}
    try:
        with _conn() as conn:
            if require_rollup:
                cur = conn.execute(
                    """
                    DELETE FROM container_metrics
                    WHERE substr(timestamp, 1, 13) < ?
                      AND substr(timestamp, 1, 13) IN
                          (SELECT hour FROM container_metrics_hourly)
                    """,
                    (cut_h,),
                )
                deleted["container_metrics"] = cur.rowcount
                cur = conn.execute(
                    """
                    DELETE FROM disk_metrics
                    WHERE substr(timestamp, 1, 10) < ?
                      AND substr(timestamp, 1, 10) IN
                          (SELECT day FROM disk_metrics_daily)
                    """,
                    (cut_d,),
                )
                deleted["disk_metrics"] = cur.rowcount
            else:
                deleted["container_metrics"] = conn.execute(
                    "DELETE FROM container_metrics WHERE substr(timestamp,1,13) < ?", (cut_h,)
                ).rowcount
                deleted["disk_metrics"] = conn.execute(
                    "DELETE FROM disk_metrics WHERE substr(timestamp,1,10) < ?", (cut_d,)
                ).rowcount
    except Exception as e:
        _log(f"prune falló: {e}")
    return deleted


def retention(keep_days: int = RAW_KEEP_DAYS, vacuum: bool = True) -> dict:
    """Política completa: agregar, purgar y recuperar espacio. Idempotente."""
    before = _db_size_mb()
    rolled = rollup(keep_days)
    deleted = prune(keep_days)
    if vacuum and (deleted["container_metrics"] or deleted["disk_metrics"]):
        try:
            conn = sqlite3.connect(DB_PATH, timeout=60)
            conn.execute("VACUUM")
            conn.close()
        except Exception as e:
            _log(f"vacuum falló: {e}")
    return {
        "cutoff_hour": _cutoff_hour(keep_days),
        "rolled_up": rolled,
        "deleted": deleted,
        "db_size_mb_before": before,
        "db_size_mb_after": _db_size_mb(),
    }


def _db_size_mb() -> float:
    try:
        return round(DB_PATH.stat().st_size / (1024**2), 1)
    except Exception:
        return -1.0


def stats() -> dict:
    """Resumen del contenido de la BD. Para /homelab y para el informe diario."""
    out = {}
    try:
        with _conn() as conn:
            out["container_metrics"] = conn.execute(
                "SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM container_metrics"
            ).fetchone()
            out["disk_metrics"] = conn.execute(
                "SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM disk_metrics"
            ).fetchone()
            out["restart_history"] = conn.execute(
                "SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM restart_history"
            ).fetchone()
            out["restart_by_result"] = dict(
                conn.execute(
                    "SELECT result, COUNT(*) FROM restart_history GROUP BY result"
                ).fetchall()
            )
            out["container_metrics_hourly"] = conn.execute(
                "SELECT COUNT(*), MIN(hour), MAX(hour) FROM container_metrics_hourly"
            ).fetchone()
            out["disk_metrics_daily"] = conn.execute(
                "SELECT COUNT(*), MIN(day), MAX(day) FROM disk_metrics_daily"
            ).fetchone()
            out["db_size_mb"] = _db_size_mb()
    except Exception as e:
        _log(f"stats falló: {e}")
    return out


# ── Autocomprobación ──────────────────────────────────────────────────────────


def _selftest() -> int:
    """Ejercita el módulo entero contra una BD temporal. No toca la real."""
    import tempfile

    global DB_PATH
    original = DB_PATH
    tmp = Path(tempfile.mkdtemp()) / "test.db"
    DB_PATH = tmp
    failures = []

    def check(label, cond):
        print(f"  {'OK  ' if cond else 'FALLO'} {label}")
        if not cond:
            failures.append(label)

    try:
        check("init_db", init_db())
        check("init_db idempotente", init_db())

        n = record_containers(
            [
                {
                    "name": "traefik",
                    "image": "traefik:v3",
                    "status": "Up 2 hours",
                    "health": "healthy",
                    "cpu_percent": 1.5,
                    "memory_mb": 48.2,
                    "memory_percent": 0.1,
                },
                {"name": "metube", "status": "Exited (1)", "health": "unhealthy"},
                {"name": None, "status": "ignorado"},  # debe descartarse
            ]
        )
        check("record_containers descarta sin nombre", n == 2)
        check("record_containers vacío", record_containers([]) == 0)

        check("record_restart success", record_restart("metube", "success", "unhealthy"))
        check("record_restart skipped", record_restart("nextcloud", "skipped", "crítico"))
        check(
            "record_disks",
            record_disks([{"path": "/", "label": "Sistema", "used_percent": 62.0, "free_gb": 120.5}])
            == 1,
        )

        s = stats()
        check("stats cuenta contenedores", s["container_metrics"][0] == 2)
        check("stats cuenta reinicios", s["restart_history"][0] == 2)
        check("stats agrupa por resultado", s["restart_by_result"] == {"success": 1, "skipped": 1})

        # ── Retención ──────────────────────────────────────────────────────
        # Tres muestras antiguas del mismo contenedor dentro de la misma hora,
        # para comprobar que la agregación calcula media, máximo y ratio.
        base = datetime.fromtimestamp(time() - 200 * 86400).replace(minute=0, second=0)
        for i, (cpu, mem, health) in enumerate(
            [(10.0, 100.0, "healthy"), (20.0, 200.0, "healthy"), (60.0, 300.0, "unhealthy")]
        ):
            record_containers(
                [{"name": "antiguo", "cpu_percent": cpu, "memory_mb": mem, "health": health}],
                timestamp=base.replace(minute=i * 5).isoformat(timespec="seconds"),
            )
        record_disks(
            [{"path": "/old", "label": "Viejo", "used_percent": 80.0, "free_gb": 10.0}],
            timestamp=base.isoformat(timespec="seconds"),
        )

        check("rollup agrega la hora antigua", rollup(30)["container_metrics_hourly"] == 1)
        check("rollup idempotente", rollup(30)["container_metrics_hourly"] == 1)

        with _conn() as conn:
            row = conn.execute(
                "SELECT samples, cpu_avg, cpu_max, memory_max_mb, healthy_ratio "
                "FROM container_metrics_hourly WHERE container='antiguo'"
            ).fetchone()
        check("agregado correcto (3 muestras, avg 30, max 60, ratio 2/3)",
              row == (3, 30.0, 60.0, 300.0, 0.6667))
        check("agregado diario de discos", stats()["disk_metrics_daily"][0] == 1)

        check("prune borra solo el detalle antiguo", prune(30)["container_metrics"] == 3)
        check("el detalle reciente sobrevive", stats()["container_metrics"][0] == 2)
        check("el agregado sobrevive a la purga", stats()["container_metrics_hourly"][0] == 1)
        check("prune respeta restart_history", stats()["restart_history"][0] == 2)

        # Red de seguridad: sin agregado previo, la purga no debe borrar nada.
        with _conn() as conn:
            conn.execute("DELETE FROM container_metrics_hourly")
        record_containers([{"name": "huerfano"}], timestamp=base.isoformat(timespec="seconds"))
        check("prune no borra lo no agregado", prune(30)["container_metrics"] == 0)
        check("prune(require_rollup=False) sí borra",
              prune(30, require_rollup=False)["container_metrics"] == 1)

        check("retention devuelve resumen", "cutoff_hour" in retention(30))

        # El contrato clave: nunca propagar excepciones.
        DB_PATH = Path("/ruta/que/no/existe/y/no/se/puede/crear/x.db")
        check("no lanza con ruta inválida", record_containers([{"name": "x"}]) == 0)

    finally:
        DB_PATH = original

    print()
    if failures:
        print(f"FALLOS: {len(failures)} → {failures}")
        return 1
    print("Todo correcto.")
    return 0


def _cli() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Métricas del homelab en SQLite.")
    ap.add_argument("--selftest", action="store_true", help="autocomprobación en BD temporal")
    ap.add_argument("--retention", action="store_true", help="agrega, purga y compacta")
    ap.add_argument("--keep-days", type=int, default=RAW_KEEP_DAYS,
                    help=f"días de detalle a 5 min (por defecto {RAW_KEEP_DAYS})")
    ap.add_argument("--dry-run", action="store_true",
                    help="con --retention: muestra el plan sin borrar")
    args = ap.parse_args()

    if args.selftest:
        return _selftest()

    init_db()

    if args.retention:
        cut = _cutoff_hour(args.keep_days)
        if args.dry_run:
            with _conn() as conn:
                n_c = conn.execute(
                    "SELECT COUNT(*) FROM container_metrics WHERE substr(timestamp,1,13) < ?",
                    (cut,),
                ).fetchone()[0]
                n_d = conn.execute(
                    "SELECT COUNT(*) FROM disk_metrics WHERE substr(timestamp,1,10) < ?",
                    (cut[:10],),
                ).fetchone()[0]
            print(f"corte: {cut}  (conservando {args.keep_days} días de detalle)")
            print(f"  container_metrics a comprimir: {n_c}")
            print(f"  disk_metrics a comprimir:      {n_d}")
            print("  simulación: nada escrito")
            return 0
        for k, v in retention(args.keep_days).items():
            print(f"  {k}: {v}")
        return 0

    for k, v in stats().items():
        print(f"{k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
