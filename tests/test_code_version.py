"""L'app si accorge dei file .py cambiati dopo l'avvio (Streamlit non ricarica i moduli importati)."""
import os

from modules import code_version


def test_file_modificati_aggiunti_e_tolti(tmp_path):
    (tmp_path / "modules").mkdir()
    (tmp_path / "ui").mkdir()
    (tmp_path / "data").mkdir()
    a = tmp_path / "modules" / "a.py"
    a.write_text("x = 1")
    (tmp_path / "data" / "big.db").write_text("non conta")
    gone = tmp_path / "ui" / "b.py"
    gone.write_text("y = 1")
    start = code_version.snapshot(tmp_path)
    assert code_version.changed(tmp_path, start) == []
    os.utime(a, (a.stat().st_atime, a.stat().st_mtime + 10))
    gone.unlink()
    (tmp_path / "data" / "db_x.py").write_text("z = 1")
    assert code_version.changed(tmp_path, start) == ["data/db_x.py", "modules/a.py", "ui/b.py"]


def test_all_avvio_nulla_e_cambiato():
    assert "modules/code_version.py" in code_version.STARTED
