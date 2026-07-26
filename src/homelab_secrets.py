#!/usr/bin/env python3
"""
homelab_secrets.py — Carga de credenciales para los scripts del homelab.

Orden de resolución, de mayor a menor prioridad:
  1. Variable de entorno
  2. Fichero de secretos en formato KEY=VALUE

Ruta del fichero: $HOMELAB_SECRETS, o por defecto
<repo>/.secrets/telegram.env (mode 600).

Contrato: ninguna función lanza excepción. Si no encuentra el valor
devuelve el default. Un monitor no debe caerse porque falte un fichero.

El módulo se llama `homelab_secrets` y no `secrets` a propósito: `secrets`
es un módulo de la stdlib y sombrearlo rompería cualquier dependencia que
lo importe.

Comprobación (no imprime valores):
    python3 homelab_secrets.py --check
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent

DEFAULT_PATH = str(_REPO_ROOT / ".secrets" / "telegram.env")

_cache: dict[str, str] | None = None


def secrets_path() -> Path:
    return Path(os.environ.get("HOMELAB_SECRETS", DEFAULT_PATH))


def _parse(path: Path, into: dict[str, str], overwrite: bool = True) -> None:
    """Vuelca un fichero KEY=VALUE en `into`. Nunca lanza."""
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            if overwrite or key not in into:
                into[key] = val.strip().strip("'\"")
    except Exception:
        pass  # fichero ausente o ilegible: se resolverá por entorno o default


def _load_file() -> dict[str, str]:
    """
    Carga el fichero principal y, además, cualquier otro `*.env` que haya en
    su mismo directorio (kuma.env, etc.). Las claves del principal ganan.

    Así cada servicio tiene su fichero en vez de un cajón de sastre, sin que
    los scripts tengan que saber en cuál está cada credencial.

    Cachea el resultado. Nunca lanza.
    """
    global _cache
    if _cache is not None:
        return _cache

    principal = secrets_path()
    values: dict[str, str] = {}
    _parse(principal, values)

    try:
        for extra in sorted(principal.parent.glob("*.env")):
            if extra != principal:
                _parse(extra, values, overwrite=False)
    except Exception:
        pass

    _cache = values
    return values


def get(key: str, default: str = "") -> str:
    """Entorno primero, luego fichero, luego default."""
    val = os.environ.get(key)
    if val:
        return val
    return _load_file().get(key, default)


def telegram() -> tuple[str, str]:
    """Devuelve (BOT_TOKEN, CHAT_ID). Cadenas vacías si no están configurados."""
    return get("TELEGRAM_BOT_TOKEN"), get("TELEGRAM_CHAT_ID")


def reload() -> None:
    """Descarta la caché. Útil en tests."""
    global _cache
    _cache = None


def _check() -> int:
    """Informa de si las credenciales se resuelven, sin revelar su valor."""
    path = secrets_path()
    print(f"fichero: {path}")
    print(f"  existe: {path.exists()}")
    if path.exists():
        mode = oct(path.stat().st_mode & 0o777)
        print(f"  permisos: {mode}{'' if mode == '0o600' else '  ← debería ser 0o600'}")

    ok = True
    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        val = get(key)
        origen = "entorno" if os.environ.get(key) else ("fichero" if val else "—")
        print(f"  {key}: {'OK' if val else 'AUSENTE'} (origen: {origen}, longitud: {len(val)})")
        ok = ok and bool(val)

    print()
    print("Credenciales resueltas." if ok else "FALTAN credenciales.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(_check() if "--check" in sys.argv else _check())
