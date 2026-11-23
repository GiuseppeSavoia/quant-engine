"""Avvio dell'API: .venv/bin/python -m api [--demo] [--config FILE] [--host 127.0.0.1] [--port 8600]

Finché non c'è il login (fase 4 di docs/piano_frontend.md) l'API ascolta solo sul Mac stesso (127.0.0.1)."""
from __future__ import annotations

import argparse

import uvicorn

from api.app import create_app


def main(argv=None) -> None:
    p = argparse.ArgumentParser(prog="python -m api", description="API di QuantEngine")
    p.add_argument("--demo", action="store_true", help="dati simulati (data/demo.db)")
    p.add_argument("--config", default=None, help="config.yaml da usare")
    p.add_argument("--host", default="127.0.0.1", help="indirizzo (predefinito: solo questo Mac)")
    p.add_argument("--port", type=int, default=8600)
    a = p.parse_args(argv)
    if a.host not in ("127.0.0.1", "localhost", "::1"):
        p.error("senza login l'API non si apre alla rete: usa 127.0.0.1")
    uvicorn.run(create_app(a.config, a.demo), host=a.host, port=a.port, log_level="info")


if __name__ == "__main__":
    main()
