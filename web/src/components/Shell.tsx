// Cornice: marchio, ricerca aziende (anche da tastiera: "/" per cercare, frecce, Invio), pagine a segmenti, aspetto
// (Sistema / Chiaro / Scuro, ricordato in questo browser; con il login diventerà una preferenza dell'utente),
// barra di stato in fondo.
import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, NavLink, useLocation, useNavigate } from "react-router-dom";
import { api, type CompanyRow, type Health } from "../lib/api";
import { Segmented } from "./ui";

type Appearance = "sistema" | "chiaro" | "scuro";
const APPEARANCE_KEY = "qe_aspetto";

function readAppearance(): Appearance {
  try {
    const v = localStorage.getItem(APPEARANCE_KEY);
    return v === "chiaro" || v === "scuro" ? v : "sistema";
  } catch {
    return "sistema";
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

function Search() {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const box = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();
  const { data } = useQuery({
    queryKey: ["search", q],
    queryFn: () => api<{ results: CompanyRow[] }>(`/companies?q=${encodeURIComponent(q)}&limit=8`),
    enabled: q.trim().length > 0,
    staleTime: 60_000,
  });
  const results = q.trim() ? data?.results ?? [] : [];

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = ["INPUT", "TEXTAREA", "SELECT"].includes((document.activeElement?.tagName ?? ""));
      if (e.key === "/" && !typing) {
        e.preventDefault();
        box.current?.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const go = (ticker: string) => {
    setQ("");
    setOpen(false);
    box.current?.blur();
    navigate(`/azienda/${encodeURIComponent(ticker)}`);
  };

  return (
    <div className="search">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2"
        strokeLinecap="round" aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
      <input ref={box} value={q} placeholder="Cerca azienda…   /" aria-label="Cerca azienda" role="combobox"
        aria-expanded={open && results.length > 0} aria-controls="search-results" aria-autocomplete="list"
        onChange={(e) => { setQ(e.target.value); setOpen(true); setActive(0); }}
        onFocus={() => setOpen(true)} onBlur={() => setTimeout(() => setOpen(false), 150)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") { e.preventDefault(); setActive((a) => Math.min(a + 1, results.length - 1)); }
          else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)); }
          else if (e.key === "Enter" && results[active]) go(results[active].ticker);
          else if (e.key === "Escape") { setOpen(false); box.current?.blur(); }
        }} />
      {open && results.length > 0 && (
        <ul className="search-results" id="search-results" role="listbox">
          {results.map((r, i) => (
            <li key={r.ticker}>
              <button type="button" role="option" aria-selected={i === active} onMouseDown={() => go(r.ticker)}>
                <span className="tk">{r.ticker}</span><span className="nm">{r.name}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// pagine meno frequenti, raccolte come nei due menu della webapp classica (Aziende / Strategie e ricerca)
export const MORE: { title: string; pages: { to: string; label: string }[] }[] = [
  { title: "Il mio lavoro", pages: [{ to: "/pipeline", label: "Pipeline" }, { to: "/diario", label: "Diario delle decisioni" },
    { to: "/metodo", label: "Il mio metodo" }] },
  { title: "Strategie e ricerca", pages: [{ to: "/strategie", label: "Strategie" }, { to: "/backtest", label: "Backtest" },
    { to: "/laboratorio", label: "Laboratorio" }, { to: "/strategia-automatica", label: "Strategia automatica" },
    { to: "/fondi", label: "Grandi investitori (13F)" }] },
  { title: "Dati", pages: [{ to: "/inserisci-dati", label: "Inserisci dati" }, { to: "/qualita", label: "Qualità dei dati" }] },
];

function MoreMenu() {
  const { pathname } = useLocation();
  const ref = useRef<HTMLDetailsElement>(null);
  useEffect(() => { if (ref.current) ref.current.open = false; }, [pathname]);
  const here = MORE.flatMap((g) => g.pages).find((p) => pathname.startsWith(p.to));
  return (
    <details className="dropdown more" ref={ref}>
      <summary className={`btn${here ? " on" : ""}`}>{here ? here.label : "Altro"} ▾</summary>
      <div className="menu">
        {MORE.map((g) => (
          <fieldset key={g.title}><legend>{g.title}</legend>
            {g.pages.map((p) => <NavLink key={p.to} to={p.to} className="menu-link">{p.label}</NavLink>)}
          </fieldset>
        ))}
      </div>
    </details>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  const [mode, setMode] = useAppearance();
  const health = useQuery({ queryKey: ["health"], queryFn: () => api<Health>("/health"), staleTime: 300_000 });
  const lastTicker = (() => { try { return localStorage.getItem("qe_ultima_azienda"); } catch { return null; } })();
  return (
    <>
      <header className="shell-top">
        <div className="shell-row">
          <Link to="/" className="brand" aria-label="QuantEngine, pagina iniziale">
            <img src={isDark(mode) ? "/logo_dark.svg" : "/logo_light.svg"} alt="QuantEngine" />
          </Link>
          <Search />
          <nav className="segmented" aria-label="Pagine">
            <NavLink to="/" end>Oggi</NavLink>
            <NavLink to="/mie-aziende">Le mie aziende</NavLink>
            <NavLink to={lastTicker ? `/azienda/${lastTicker}` : "/azienda"}>Scheda</NavLink>
            <NavLink to="/esplora">Esplora</NavLink>
            <NavLink to="/confronta">Confronta</NavLink>
            <NavLink to="/portafoglio">Portafoglio</NavLink>
            <NavLink to="/notizie">Notizie</NavLink>
          </nav>
          <MoreMenu />
          <span className="spacer" />
          <Segmented label="Aspetto" value={mode} onChange={setMode}
            options={[{ value: "sistema", label: "Sistema" }, { value: "chiaro", label: "Chiaro" },
              { value: "scuro", label: "Scuro" }]} />
        </div>
      </header>
      <main>{children}</main>
      <footer className="status">
        {health.data?.demo && <span className="demo-pill">DEMO</span>}
        <span>{health.isError ? "API non raggiungibile" : health.data ? <>dati: <b>{health.data.database}</b></> : "…"}</span>
        <span>Il giudizio è una sintesi di criteri fissi, non una raccomandazione.</span>
      </footer>
    </>
  );
}
