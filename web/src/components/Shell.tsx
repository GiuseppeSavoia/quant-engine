// Cornice (stile "terminale da prodotto", 2026-11-24): marchio, ricerca a comandi (⌘K, Ctrl+K o "/": CommandPalette),
// pagine, menu Altro, aspetto (Scuro predefinito / Chiaro / Sistema, ricordato in questo browser; con il login diventerà
// una preferenza dell'utente), barra di stato in fondo.
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, NavLink, useLocation } from "react-router-dom";
import { api, type Health, type MarketStrip } from "../lib/api";
import { fmt } from "../lib/fmt";
import { Spark } from "./Spark";
import { CommandPalette, useCommandPalette } from "./CommandPalette";
import { Segmented } from "./ui";
import { LANG, setLang, t, type Lang } from "../lib/i18n";

type Appearance = "sistema" | "chiaro" | "scuro";
const APPEARANCE_KEY = "qe_aspetto";

function readAppearance(): Appearance {
  // ?aspetto=chiaro|scuro|sistema nell'indirizzo (demo, materiale di vendita) prevale ed è ricordato
  const fromUrl = typeof location !== "undefined" ? new URLSearchParams(location.search).get("aspetto") : null;
  if (fromUrl === "chiaro" || fromUrl === "scuro" || fromUrl === "sistema") return fromUrl;
  try {
    const v = localStorage.getItem(APPEARANCE_KEY);
    return v === "chiaro" || v === "sistema" ? v : "scuro";  // scuro = aspetto del prodotto
  } catch {
    return "scuro";
  }
}

function useAppearance(): [Appearance, (a: Appearance) => void] {
  const [mode, setMode] = useState<Appearance>(readAppearance);
  useEffect(() => {
    const root = document.documentElement;
    if (mode === "sistema") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", mode === "scuro" ? "dark" : "light");
    try {
      localStorage.setItem(APPEARANCE_KEY, mode);
    } catch {
      /* archiviazione non disponibile: vale per questa sessione */
    }
  }, [mode]);
  return [mode, setMode];
}

function isDark(mode: Appearance): boolean {
  if (mode !== "sistema") return mode === "scuro";
  return typeof matchMedia === "function" && matchMedia("(prefers-color-scheme: dark)").matches;
}

function SearchButton({ onOpen }: { onOpen: () => void }) {
  const mac = typeof navigator !== "undefined" && /Mac/i.test(navigator.platform);
  return (
    <button type="button" className="search-trigger" onClick={onOpen} aria-label={t("Cerca azienda, ticker o comando")}>
      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
      <span>{t("Cerca azienda, ticker o comando")}</span>
      <kbd>{mac ? "⌘K" : "Ctrl K"}</kbd>
    </button>
  );
}

// pagine meno frequenti, raccolte come nei due menu della webapp classica (Aziende / Strategie e ricerca)
export const MORE: { title: string; pages: { to: string; label: string }[] }[] = [
  { title: "Il mio lavoro", pages: [{ to: "/pipeline", label: "Pipeline" }, { to: "/diario", label: "Diario delle decisioni" },
    { to: "/metodo", label: "Il mio metodo" }] },
  { title: "Strategie e ricerca", pages: [{ to: "/strategie", label: "Strategie" }, { to: "/backtest", label: "Backtest" },
    { to: "/laboratorio", label: "Laboratorio" }, { to: "/strategia-automatica", label: "Strategia automatica" },
    { to: "/fondi", label: "Grandi investitori (13F)" }] },
  { title: "Dati", pages: [{ to: "/inserisci-dati", label: "Inserisci dati" }, { to: "/qualita", label: "Qualità dei dati" },
    { to: "/limiti", label: "Metodo e limiti" }] },
];

function MoreMenu() {
  const { pathname } = useLocation();
  const ref = useRef<HTMLDetailsElement>(null);
  useEffect(() => { if (ref.current) ref.current.open = false; }, [pathname]);
  const here = MORE.flatMap((g) => g.pages).find((p) => pathname.startsWith(p.to));
  return (
    <details className="dropdown more" ref={ref}>
      <summary className={`nav-more${here ? " active" : ""}`}>{here ? t(here.label) : t("Altro")} <span aria-hidden="true">▾</span></summary>
      <div className="menu">
        {MORE.map((g) => (
          <fieldset key={g.title}><legend>{t(g.title)}</legend>
            {g.pages.map((p) => <NavLink key={p.to} to={p.to} className="menu-link">{t(p.label)}</NavLink>)}
          </fieldset>
        ))}
      </div>
    </details>
  );
}

function MarketBar() {
  const q = useQuery({ queryKey: ["market-strip"], queryFn: () => api<MarketStrip>("/market-strip"), staleTime: 300_000, retry: false });
  if (!q.data || q.data.items.length === 0) return null;
  return (
    <div className="market-bar" aria-label={t("Mercati (ultime chiusure)")}>
      <div className="market-row">
        {q.data.items.map((it) => (
          <div key={it.key} className="mk">
            <span className="l">{it.label}</span>
            {it.key === "BREADTH" ? (
              <span className="v">{fmt(it.value, "pct", { digits: 0 })} <span className="d">{it.detail} · {t("max 52s {highs} · min {lows}", { highs: it.highs, lows: it.lows })}</span></span>
            ) : (
              <>
                <span className="v">{fmt(it.value, it.kind === "price" ? "price" : it.kind === "pct" ? "pct" : "num", it.kind === "pct" ? { digits: 2 } : {})}</span>
                {it.change !== null && (it.change_kind === "bp"
                  ? <span className={`c ${it.change >= 0 ? "pos" : "neg"}`}>{t("{v} pb", { v: fmt(it.change, "num", { digits: 0, signed: true }) })}</span>
                  : <span className={`c ${it.change >= 0 ? "pos" : "neg"}`}>{fmt(it.change, "pct", { signed: true, digits: 2 })}</span>)}
                {it.spark && <Spark values={it.spark} width={56} height={16} />}
              </>
            )}
          </div>
        ))}
        <span className="mk-date">{t("chiusure del {date}", { date: fmt(q.data.as_of?.slice(0, 10) ?? null, "date_short") })}</span>
      </div>
    </div>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  const [mode, setMode] = useAppearance();
  const [paletteOpen, setPaletteOpen] = useCommandPalette();
  const health = useQuery({ queryKey: ["health"], queryFn: () => api<Health>("/health"), staleTime: 300_000 });
  const lastTicker = (() => { try { return localStorage.getItem("qe_ultima_azienda"); } catch { return null; } })();
  return (
    <>
      <header className="shell-top">
        <div className="shell-row">
          <Link to="/" className="brand" aria-label={t("QuantEngine, pagina iniziale")}>
            <img src={isDark(mode) ? "/logo_dark.svg" : "/logo_light.svg"} alt="QuantEngine" />
          </Link>
          <nav className="topnav" aria-label={t("Pagine")}>
            <NavLink to="/" end>{t("Oggi")}</NavLink>
            <NavLink to="/mie-aziende">{t("Le mie aziende")}</NavLink>
            <NavLink to={lastTicker ? `/azienda/${lastTicker}` : "/azienda"}>{t("Scheda")}</NavLink>
            <NavLink to="/esplora">{t("Esplora")}</NavLink>
            <NavLink to="/confronta">{t("Confronta")}</NavLink>
            <NavLink to="/portafoglio">{t("Portafoglio")}</NavLink>
            <NavLink to="/notizie">{t("Notizie")}</NavLink>
            <MoreMenu />
          </nav>
          <span className="spacer" />
          <SearchButton onOpen={() => setPaletteOpen(true)} />
          <Segmented label={t("Aspetto")} value={mode} onChange={setMode}
            options={[{ value: "scuro", label: t("Scuro") }, { value: "chiaro", label: t("Chiaro") }, { value: "sistema", label: t("Auto") }]} />
          <Segmented label={t("Lingua")} value={LANG} onChange={(v: Lang) => setLang(v)}
            options={[{ value: "en", label: "EN" }, { value: "it", label: "IT" }]} />
        </div>
        <MarketBar />
      </header>
      {paletteOpen && <CommandPalette onClose={() => setPaletteOpen(false)} />}
      <main>{children}</main>
      <footer className="status">
        {health.data?.demo && <span className="demo-pill">DEMO</span>}
        <span className="live" aria-hidden="true" />
        <span>{health.isError ? t("API non raggiungibile") : health.data
          ? (health.data.last_update ? <>{t("aggiornato il")} <b>{fmt(health.data.last_update.slice(0, 10), "date_short")} {health.data.last_update.slice(11, 16)}</b></> : <>{t("dati")} <b>{health.data.database}</b></>)
          : "…"}</span>
        <span className="sep">{t("Analisi a criteri fissi, non consulenza né raccomandazione")} · <Link to="/limiti">{t("metodo e limiti")}</Link></span>
      </footer>
    </>
  );
}
