"""
QUANT_ENGINE come app desktop: avvia la webapp Streamlit in background e la mostra in una finestra nativa
(pywebview, WebKit di macOS). Chiudendo la finestra si ferma anche il server.

    .venv/bin/python desktop.py                 # dati reali
    .venv/bin/python desktop.py --demo          # dati simulati

Senza pywebview si apre il browser predefinito (il server resta acceso finché non si chiude con Ctrl+C).
L'icona per il Dock e il Launchpad la crea `python tools/build_mac_app.py`.
I dati mostrati sono quelli dell'ultima esecuzione di main.py (la webapp non scarica nulla).
"""
from __future__ import annotations

import argparse
import logging
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP_NAME = "QUANT_ENGINE"
LOG_PATH = Path.home() / "Library" / "Logs" / f"{APP_NAME}.log"
STARTUP_TIMEOUT = 90  # secondi: il primo avvio carica 18 anni di prezzi dal database

logger = logging.getLogger(APP_NAME)

LOADING_HTML = """<html><body style="font-family:-apple-system;display:flex;height:100vh;margin:0;
align-items:center;justify-content:center;background:#f7f7f5;color:#3a3a38">
<div style="text-align:center"><div style="font-size:28px;font-weight:600">QUANT_ENGINE</div>
<div style="margin-top:12px">Avvio in corso: carico prezzi e bilanci dal database…</div></div></body></html>"""


def free_port(preferred: int = 8502) -> int:
    """La porta preferita se libera (8501 resta alla webapp di sviluppo), altrimenti una a caso."""
    for port in (preferred, 0):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("nessuna porta libera")


def start_server(port: int, demo: bool, log_file) -> subprocess.Popen:
    # il server gira con il Python del progetto (nell'app, sys.executable è la copia usata per la finestra)
    venv_python = ROOT / ".venv" / "bin" / "python"
    python = str(venv_python) if venv_python.exists() else sys.executable
    cmd = [python, str(ROOT / "desktop.py"), "--serve", "run", str(ROOT / "app.py"),
           "--server.headless", "true", "--server.port", str(port), "--server.address", "127.0.0.1",
           "--browser.gatherUsageStats", "false", "--client.toolbarMode", "minimal",
           "--server.runOnSave", "false"]
    if demo:
        cmd += ["--", "--demo"]
    return subprocess.Popen(cmd, cwd=ROOT, stdout=log_file, stderr=subprocess.STDOUT)


def serve(streamlit_args: list[str]) -> None:
    """Processo del server: Streamlit più un controllo che lo chiude se l'app sparisce (es. Cmd+Q, che
    termina l'app senza passare dal codice di chiusura). Un processo orfano passa a launchd (ppid 1)."""
    import os
    import threading

    parent = os.getppid()

    def watch():
        while os.getppid() == parent:
            time.sleep(2)
        os._exit(0)

    threading.Thread(target=watch, daemon=True).start()
    from streamlit.web import cli

    sys.argv = ["streamlit", *streamlit_args]
    sys.exit(cli.main())


def wait_until_ready(url: str, server: subprocess.Popen, timeout: float = STARTUP_TIMEOUT) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if server.poll() is not None:
            return False  # il server è uscito (errore nel log)
        try:
            with urllib.request.urlopen(f"{url}/_stcore/health", timeout=2) as r:
                if r.status == 200:
                    return True
        except OSError:
            pass
        time.sleep(0.3)
    return False


def _mac_identity(icon: Path) -> None:
    """Nome e icona nel Dock: senza, macOS mostrerebbe "Python" con il razzo."""
    try:
        from AppKit import NSApplication, NSImage
        from Foundation import NSBundle
    except ImportError:
        return
    info = NSBundle.mainBundle().infoDictionary()
    if info is not None:
        info["CFBundleName"] = APP_NAME
    if icon.exists():
        image = NSImage.alloc().initWithContentsOfFile_(str(icon))
        if image is not None:
            NSApplication.sharedApplication().setApplicationIconImage_(image)


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--serve":  # uso interno: il processo del server
        serve(sys.argv[2:])
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--demo", action="store_true", help="dati simulati (data/demo.db)")
    args = parser.parse_args()

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=LOG_PATH, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    with open(LOG_PATH, "a", encoding="utf-8") as log_file:
        logger.info("Avvio del server su %s", url)
        server = start_server(port, args.demo, log_file)
        try:
            try:
                import webview
            except ImportError:
                logger.warning("pywebview non installato: apro il browser")
                if wait_until_ready(url, server):
                    webbrowser.open(url)
                server.wait()
                return 0

            window = webview.create_window(APP_NAME, html=LOADING_HTML, width=1440, height=920, min_size=(900, 600))

            def load():
                if wait_until_ready(url, server):
                    window.load_url(url)
                else:
                    window.load_html(LOADING_HTML.replace(
                        "Avvio in corso: carico prezzi e bilanci dal database…",
                        f"Avvio non riuscito. Dettagli in {LOG_PATH}"))

            if sys.platform == "darwin":
                _mac_identity(ROOT / "tools" / "icon.png")
            webview.start(load)
        finally:
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
            logger.info("Server fermato")
    return 0


if __name__ == "__main__":
    sys.exit(main())
