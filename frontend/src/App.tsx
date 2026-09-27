import { Route, Routes } from "react-router-dom";
import Layout from "./components/Layout";
import DashboardPage from "./pages/DashboardPage";
import InventoryPage from "./pages/InventoryPage";
import AgentsPage from "./pages/AgentsPage";
import CasePage from "./pages/CasePage";
import ImpactPage from "./pages/ImpactPage";
import SimulationPage from "./pages/SimulationPage";
import ChaosPage from "./pages/ChaosPage";
import WarehousePage from "./pages/WarehousePage";
import ScenariosPage from "./pages/ScenariosPage";
import AboutPage from "./pages/AboutPage";
import DatasetsPage from "./pages/DatasetsPage";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/inventory" element={<InventoryPage />} />
        <Route path="/agents" element={<AgentsPage />} />
        <Route path="/cases/:sku" element={<CasePage />} />
        <Route path="/impact" element={<ImpactPage />} />
        <Route path="/simulation/:sku" element={<SimulationPage />} />
        <Route path="/chaos" element={<ChaosPage />} />
        <Route path="/warehouse" element={<WarehousePage />} />
        <Route path="/simulations" element={<ScenariosPage />} />
        <Route path="/about" element={<AboutPage />} />
        <Route path="/datasets" element={<DatasetsPage />} />
      </Route>
    </Routes>
  );
}
