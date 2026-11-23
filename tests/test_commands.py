"""Test della barra dei comandi (stile terminale): testo -> pagina da aprire."""
from modules.commands import PAGES, parse

TICKERS = ["AAPL", "MSFT", "GOOGL", "GOOG", "KO", "BRK-B", "A", "T", "PEP"]
NAMES = {"AAPL": "Apple Inc.", "MSFT": "Microsoft", "GOOGL": "Alphabet Inc. (Class A)",
         "GOOG": "Alphabet Inc. (Class C)", "KO": "Coca-Cola Company (The)", "BRK-B": "Berkshire Hathaway",
         "A": "Agilent Technologies", "T": "AT&T", "PEP": "PepsiCo"}


def p(text):
    return parse(text, TICKERS, NAMES)


def test_company_by_ticker_or_name():
    assert (p("aapl").page, p("aapl").tickers) == ("azienda", ["AAPL"])
    assert p("apple").tickers == ["AAPL"] and p("berkshire hathaway").tickers == ["BRK-B"]
    assert p("brk.b").tickers == ["BRK-B"]                        # il punto come su Yahoo/SEC
    assert p('"coca-cola"').tickers == ["KO"] and p("coca").tickers == ["KO"]
    assert p("A").tickers == ["A"] and p("t").tickers == ["T"]   # ticker di una lettera, non sigle di pagina


def test_compare_follow_pages_help_and_errors():
    assert (p("aapl msft").page, p("aapl msft").tickers) == ("confronta", ["AAPL", "MSFT"])
    assert p("cmp aapl msft goog").tickers == ["AAPL", "MSFT", "GOOG"]
    assert p("aapl msft goog googl ko pep").error                 # più di 5
    c = p("segui ko")
    assert c.follow is True and c.tickers == ["KO"] and p("togli ko").follow is False
    assert p("segui").error
    assert p("13f").page == "fondi" and p("eqs").page == "esplora" and p("home").page == ""
    assert set(PAGES) >= {"", "azienda", "confronta", "fondi", "strategie", "backtest", "qualita"}
    assert p("aiuto").help and p("?").help
    assert "più aziende" in p("alphabet").error and "GOOG" in p("alphabet").error
    assert "Non trovo" in p("xyz").error and p("").error


def test_company_functions_and_menu_numbers():
    c = p("aapl fa")
    assert (c.page, c.tickers, c.section) == ("azienda", ["AAPL"], "FA")
    assert p("fa").section == "FA" and p("fa").tickers == []          # sull'azienda già aperta
    assert p("7").section == "ERN" and p("/8").section == "DVD"         # menu numerato; "/" iniziale ignorato
    assert p("1").section == "PRV" and p("4").section == "DCF" and p("11").section == "DES"
    assert p("13").section == "EE" and p("14").section == "TAV" and p("15").error and p("0").error  # 14 = Tavolo
    assert p("aapl inv").section == "TAV" and p("tavolo").section == "TAV"
    assert p("verdetto").section == "PRV" and p("aapl prove").tickers == ["AAPL"] and p("metriche").section == "DES"
    assert p("ins").section == "HDS" and p("rv ko").tickers == ["KO"]
    assert p("aapl cn").section == "CF" and p("cn").page == "notizie"   # CN: notizie dell'azienda o tutte
    assert p("aapl auto").section == "AUTO" and p("auto").page == "strategia-automatica"
    assert p("monitor").page == "" and p("mie").page == "mie-aziende"


def test_full_name_with_a_ticker_inside():
    """Il nome completo mostrato dall'app si apre anche se una parola è il ticker di un'azienda (collaudo 2026-11-16)."""
    tickers = ["AAPL", "AMZN", "MSFT"]
    names = {t: f"Demo {t}" for t in tickers}
    cmd = parse("Demo AMZN", tickers, names)
    assert cmd.page == "azienda" and cmd.tickers == ["AMZN"] and cmd.error is None
    assert parse("demo   amzn", tickers, names).tickers == ["AMZN"]
    assert parse("AAPL MSFT", tickers, names).page == "confronta"  # due ticker restano un confronto
