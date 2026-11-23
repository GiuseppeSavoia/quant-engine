"""L'interfaccia web (web/) usa token di colore e casi di formato generati da Python (tools/export_web_shared.py):
se ui/common.py o modules/formats.py cambiano, questi file vanno rigenerati, e il test lo dice."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))


def test_web_tokens_and_fmt_cases_are_current():
    import export_web_shared as ex

    stale = [str(p.relative_to(ROOT)) for p, text in ex.expected().items()
             if not p.exists() or p.read_text(encoding="utf-8") != text]
    assert not stale, f"Rigenerare con .venv/bin/python tools/export_web_shared.py: {stale}"


def test_fmt_cases_cover_the_tricky_rules():
    import export_web_shared as ex

    cases = {(repr(c["v"]), c["kind"], tuple(sorted(c["opt"].items()))): c["out"] for c in ex.fmt_cases()}
    assert cases[("0.125", "num", ())] == "0,12"                 # metà esatta: verso il pari, come round()
    assert cases[("-0.0001", "pct", (("signed", True),))] == "0,0%"  # lo zero non ha segno
    assert cases[("66300000000.0", "money", ())] == "66,3 mld"
    assert cases[("None", "price", ())] == "–"
