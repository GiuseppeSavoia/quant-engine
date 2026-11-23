"""
Crea QUANT_ENGINE.app: un'app per macOS che si apre con un clic (Dock, Launchpad, Spotlight).

    .venv/bin/python tools/build_mac_app.py                  # in ~/Applications
    .venv/bin/python tools/build_mac_app.py --dest ~/Desktop
    .venv/bin/python tools/build_mac_app.py --demo           # app con i dati simulati

L'app non contiene il progetto: lancia `desktop.py` di questa cartella con il Python di `.venv`.
Se il progetto si sposta, va ricreata. L'icona (tools/icon.png) viene disegnata qui, senza file esterni.
"""
from __future__ import annotations

import argparse
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_NAME = "QUANT_ENGINE"
ICON_PNG = ROOT / "tools" / "icon.png"
ICON_SIZES = [16, 32, 128, 256, 512]  # ognuna anche @2x


def draw_icon(path: Path, size: int = 1024) -> None:
    """Riquadro arrotondato blu (stile macOS) con barre e una linea che sale."""
    from PIL import Image, ImageDraw

    s = 4  # disegno in grande e riduco: bordi lisci
    big = size * s
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    margin, radius = int(big * 0.098), int(big * 0.18)  # proporzioni della griglia delle icone macOS
    box = (margin, margin, big - margin, big - margin)
    top, bottom = (31, 79, 153), (42, 120, 214)
    grad = Image.new("RGBA", (big, big))
    gd = ImageDraw.Draw(grad)
    for y in range(big):
        t = y / big
        gd.line([(0, y), (big, y)], fill=tuple(int(a + (b - a) * t) for a, b in zip(top, bottom)) + (255,))
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).rounded_rectangle(box, radius=radius, fill=255)
    img.paste(grad, (0, 0), mask)

    bars = Image.new("RGBA", (big, big), (0, 0, 0, 0))  # livello a parte: la trasparenza si fonde col blu
    d = ImageDraw.Draw(bars)
    inner = big - 2 * margin
    x0, y_base = margin + inner * 0.16, margin + inner * 0.80
    bar_w, gap = inner * 0.11, inner * 0.04
    heights = [0.20, 0.30, 0.26, 0.42, 0.55]
    for i, h in enumerate(heights):
        x = x0 + i * (bar_w + gap)
        d.rounded_rectangle((x, y_base - inner * h, x + bar_w, y_base), radius=int(bar_w * 0.25),
                            fill=(255, 255, 255, 70))
    img = Image.alpha_composite(img, bars)
    d = ImageDraw.Draw(img)
    pts = [(x0 + i * (bar_w + gap) + bar_w / 2, y_base - inner * (h + 0.08)) for i, h in enumerate(heights)]
    d.line(pts, fill=(255, 255, 255, 255), width=int(inner * 0.035), joint="curve")
    r = inner * 0.035
    for x, y in pts:
        d.ellipse((x - r, y - r, x + r, y + r), fill=(255, 255, 255, 255))
    img.resize((size, size), Image.LANCZOS).save(path)


def build_icns(png: Path, dest: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "AppIcon.iconset"
        iconset.mkdir()
        for px in ICON_SIZES:
            for scale, suffix in ((1, ""), (2, "@2x")):
                out = iconset / f"icon_{px}x{px}{suffix}.png"
                subprocess.run(["sips", "-z", str(px * scale), str(px * scale), str(png), "--out", str(out)],
                               check=True, capture_output=True)
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(dest)], check=True)


def build_app(dest_dir: Path, demo: bool = False) -> Path:
    python = ROOT / ".venv" / "bin" / "python"
    if not python.exists():
        raise SystemExit(f"Manca {python}: crea prima l'ambiente (python3 -m venv .venv && "
                         ".venv/bin/pip install -r requirements.txt)")
    name = APP_NAME + (" demo" if demo else "")
    app = dest_dir.expanduser() / f"{name}.app"
    if app.exists():
        shutil.rmtree(app)  # si ricrea da zero (contiene solo file generati qui)
    macos, resources = app / "Contents" / "MacOS", app / "Contents" / "Resources"
    macos.mkdir(parents=True)
    resources.mkdir()

    if not ICON_PNG.exists():
        draw_icon(ICON_PNG)
    build_icns(ICON_PNG, resources / "AppIcon.icns")

    # Copia dell'interprete DENTRO l'app: macOS riconosce il processo come QUANT_ENGINE (nome nel menu,
    # icona nel Dock). Lanciando il Python di Homebrew si vedrebbe "Python" con il razzo.
    interpreter = macos / APP_NAME
    shutil.copy2(_gui_python(python), interpreter)
    site_packages = _site_packages(python)
    launcher = macos / "launcher"
    argv = ["desktop.py"] + (["--demo"] if demo else [])
    boot = (f"import runpy, site, sys; site.addsitedir({str(site_packages)!r}); sys.argv = {argv!r}; "
            f"runpy.run_path({str(ROOT / 'desktop.py')!r}, run_name='__main__')")
    launcher.write_text(
        "#!/bin/bash\n"
        f"# generato da tools/build_mac_app.py: avvia {ROOT / 'desktop.py'}\n"
        f'cd "{ROOT}" || exit 1\n'
        f'exec "$(dirname "$0")/{interpreter.name}" -c "{boot}"\n',
        encoding="utf-8")
    launcher.chmod(0o755)

    with open(app / "Contents" / "Info.plist", "wb") as f:
        plistlib.dump({
            "CFBundleName": name,
            "CFBundleDisplayName": name,
            "CFBundleIdentifier": "local.quantengine" + (".demo" if demo else ""),
            "CFBundleExecutable": launcher.name,
            "CFBundleIconFile": "AppIcon",
            "CFBundlePackageType": "APPL",
            "CFBundleShortVersionString": "1.0",
            "CFBundleVersion": "1",
            "LSMinimumSystemVersion": "11.0",
            "NSHighResolutionCapable": True,
        }, f)
    # firma locale (ad hoc) dell'intero pacchetto: l'interprete copiato non è più firmato come Python.app
    subprocess.run(["codesign", "--force", "--deep", "--sign", "-", str(app)], check=True, capture_output=True)
    subprocess.run(["touch", str(app)], check=False)  # il Finder aggiorna subito l'icona
    return app


def _python_info(python: Path, expr: str) -> str:
    return subprocess.run([str(python), "-c", f"import sys, sysconfig; print({expr})"],
                          check=True, capture_output=True, text=True).stdout.strip()


def _gui_python(python: Path) -> Path:
    """L'eseguibile "vero" del Python di sistema: in una build framework (Homebrew, python.org) è quello
    dentro Python.app, l'unico che può aprire finestre."""
    base = Path(_python_info(python, "sys.base_prefix"))
    app_binary = base / "Resources" / "Python.app" / "Contents" / "MacOS" / "Python"
    return app_binary if app_binary.exists() else Path(_python_info(python, "sys.executable")).resolve()


def _site_packages(python: Path) -> Path:
    return Path(_python_info(python, "sysconfig.get_paths()['purelib']"))


def main() -> None:
    if sys.platform != "darwin":
        raise SystemExit("Solo per macOS")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dest", default="~/Applications", help="cartella in cui creare l'app")
    parser.add_argument("--demo", action="store_true", help="app che apre i dati simulati")
    parser.add_argument("--redraw-icon", action="store_true", help="ridisegna tools/icon.png")
    args = parser.parse_args()
    if args.redraw_icon and ICON_PNG.exists():
        ICON_PNG.unlink()
    dest = Path(args.dest).expanduser()
    dest.mkdir(parents=True, exist_ok=True)
    app = build_app(dest, args.demo)
    print(f"Creata {app}")
    print("Trascinala nel Dock per averla sempre a portata di clic.")


if __name__ == "__main__":
    main()
