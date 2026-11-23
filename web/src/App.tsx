import { Navigate, Route, Routes } from "react-router-dom";
import { Shell } from "./components/Shell";
import { Empty } from "./components/ui";
import { CatalogPage } from "./pages/Catalog";
import { CompaniesPage } from "./pages/Companies";
import { CompanyPage } from "./pages/Company";
import { ComparePage } from "./pages/Compare";
import { ExplorePage } from "./pages/Explore";
import { JournalPage } from "./pages/Journal";
import { MethodPage } from "./pages/Method";
import { NewsPage } from "./pages/News";
import { BacktestPage } from "./pages/Backtest";
import { LabPage } from "./pages/Lab";
import { FundsPage } from "./pages/Funds";
import { QualityPage } from "./pages/Quality";
import { DataEntryPage } from "./pages/DataEntry";
import { AutoStrategyPage } from "./pages/AutoStrategy";
import { PipelinePage } from "./pages/Pipeline";
import { StrategiesPage } from "./pages/Strategies";
import { PortfolioPageView } from "./pages/Portfolio";
import { MyCompaniesPage } from "./pages/MyCompanies";
import { TodayPage } from "./pages/TodayPage";

export function App() {
  return (
    <Shell>
      <Routes>
        <Route path="/" element={<TodayPage />} />
        <Route path="/aziende" element={<CompaniesPage />} />
        <Route path="/azienda" element={<CompanyPage />} />
        <Route path="/azienda/:ticker" element={<CompanyPage />} />
        <Route path="/mie-aziende" element={<MyCompaniesPage />} />
        <Route path="/esplora" element={<ExplorePage />} />
        <Route path="/confronta" element={<ComparePage />} />
        <Route path="/diario" element={<JournalPage />} />
        <Route path="/metodo" element={<MethodPage />} />
        <Route path="/portafoglio" element={<PortfolioPageView />} />
        <Route path="/notizie" element={<NewsPage />} />
        <Route path="/strategie" element={<StrategiesPage />} />
        <Route path="/pipeline" element={<PipelinePage />} />
        <Route path="/backtest" element={<BacktestPage />} />
        <Route path="/laboratorio" element={<LabPage />} />
        <Route path="/fondi" element={<FundsPage />} />
        <Route path="/qualita" element={<QualityPage />} />
        <Route path="/inserisci-dati" element={<DataEntryPage />} />
        <Route path="/strategia-automatica" element={<AutoStrategyPage />} />
        <Route path="/componenti" element={<CatalogPage />} />
        <Route path="/index.html" element={<Navigate to="/" replace />} />
        <Route path="*" element={<div className="page"><Empty what="Pagina inesistente" why="Usa il menu in alto." /></div>} />
      </Routes>
    </Shell>
  );
}
