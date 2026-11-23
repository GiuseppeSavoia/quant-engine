import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from data.db_manager import DBManager  # noqa: E402
from modules.config import default_config  # noqa: E402
from modules.data_ingestor import DataIngestor  # noqa: E402
from modules.demo_data import DemoSource  # noqa: E402
from modules.market_data import MarketData  # noqa: E402
from modules.universe import UniverseManager  # noqa: E402



@pytest.fixture
def cfg(tmp_path):
    c = default_config()
    c["data"]["db_path"] = str(tmp_path / "test.db")
    c["data"]["facts_db_path"] = str(tmp_path / "facts.db")  # mai l'archivio vero
    c["data"]["price_chunk_pause"] = 0
    return c


@pytest.fixture
def demo():
    return DemoSource(end="2026-10-30", n_tickers=30)


@pytest.fixture(scope="session")
def demo_db_template(tmp_path_factory):
    """Database demo costruito una volta sola (scaricare e leggere i bilanci finti costa qualche secondo)."""
    c = default_config()
    c["data"]["db_path"] = str(tmp_path_factory.mktemp("demo") / "demo.db")
    c["data"]["price_chunk_pause"] = 0
    c["universe"]["source"] = "sp500"
    demo = DemoSource(end="2026-10-30", n_tickers=30)
    db = DBManager(c["data"]["db_path"])
    universe = UniverseManager(db, c, provider=demo)
    universe.refresh()
    DataIngestor(db, c, price_source=demo, fundamentals_source=demo, sleep=lambda s: None) \
        .update_all(universe.data_tickers())  # come main.py: membri attuali + ex membri
    return Path(c["data"]["db_path"])


@pytest.fixture
def demo_db(cfg, demo_db_template):
    """Copia privata del database demo per ogni test (i test possono scriverci)."""
    cfg["universe"]["source"] = "sp500"
    shutil.copyfile(demo_db_template, cfg["data"]["db_path"])
    return DBManager(cfg["data"]["db_path"])


@pytest.fixture
def universe(cfg, demo_db):
    return UniverseManager(demo_db, cfg)


@pytest.fixture
def demo_data(cfg, demo_db, universe):
    return MarketData.from_db(demo_db, cfg, universe.data_tickers())
