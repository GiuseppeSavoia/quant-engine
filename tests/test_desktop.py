"""Test dell'app desktop (senza aprire finestre)."""
import plistlib
import socket
import sys

import pytest

import desktop


def test_free_port_avoids_busy_one():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        busy = s.getsockname()[1]
        assert desktop.free_port(busy) != busy


@pytest.mark.skipif(sys.platform != "darwin", reason="solo macOS (sips, iconutil, codesign)")
def test_build_mac_app(tmp_path, monkeypatch):
    from tools import build_mac_app as b

    monkeypatch.setattr(b, "ICON_PNG", tmp_path / "icon.png")
    app = b.build_app(tmp_path, demo=True)
    info = plistlib.loads((app / "Contents" / "Info.plist").read_bytes())
    assert info["CFBundleName"] == "QUANT_ENGINE demo" and info["CFBundleExecutable"] == "launcher"
    assert (app / "Contents" / "Resources" / "AppIcon.icns").stat().st_size > 10_000
    launcher = (app / "Contents" / "MacOS" / "launcher").read_text()
    assert "desktop.py" in launcher and "--demo" in launcher
