import { createContext, useContext, useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { IDENTITIES, Identity } from "./api";
import Dashboard from "./pages/Dashboard";
import MediaKit from "./pages/MediaKit";
import PackageDetail from "./pages/PackageDetail";
import ProductDetail from "./pages/ProductDetail";
import Audiences from "./pages/Audiences";
import LiveFeed from "./pages/LiveFeed";

// Identity is app-wide: the selected buyer tier drives every priced call.
interface Ctx { identity: Identity; setIdentity: (i: Identity) => void; seller: string; }
const AppCtx = createContext<Ctx>({ identity: IDENTITIES[0], setIdentity: () => {}, seller: "" });
export const useApp = () => useContext(AppCtx);

export default function App() {
  const [identity, setIdentity] = useState<Identity>(IDENTITIES[0]);
  const [seller, setSeller] = useState("");

  useEffect(() => {
    fetch("/healthz")
      .then((r) => r.json())
      .then((d) => setSeller(d.seller ?? ""))
      .catch(() => {});
  }, []);

  return (
    <AppCtx.Provider value={{ identity, setIdentity, seller }}>
      <div className="shell">
        <header className="topbar">
          <div className="brand">
            <span className="brand-mark">A</span>
            <div className="brand-text">
              <span className="brand-title">AAMP Seller Cockpit</span>
              <span className="brand-sub">{seller || "agentic deal desk"} · powered by Databricks</span>
            </div>
          </div>
          <nav className="mainnav">
            <NavLink to="/dashboard" className={({ isActive }) => (isActive ? "active" : "")}>Dashboard</NavLink>
            <NavLink to="/media-kit" className={({ isActive }) => (isActive ? "active" : "")}>Media Kit</NavLink>
            <NavLink to="/audiences" className={({ isActive }) => (isActive ? "active" : "")}>Audiences</NavLink>
            <NavLink to="/live-feed" className={({ isActive }) => (isActive ? "active" : "")}>Live Feed</NavLink>
          </nav>
          <div className="scenario">
            <div className="identity">
              <span className="identity-label">Buyer</span>
              <div className="identity-pills">
                {IDENTITIES.map((i) => (
                  <button key={i.key} className={i.key === identity.key ? "active" : ""} onClick={() => setIdentity(i)}>
                    {i.label}
                  </button>
                ))}
              </div>
            </div>
          </div>
        </header>

        <main className="content">
          <Routes>
            <Route path="/" element={<Navigate to="/dashboard" replace />} />
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/media-kit" element={<MediaKit />} />
            <Route path="/media-kit/:packageId" element={<PackageDetail />} />
            <Route path="/media-kit/:packageId/product/:productId" element={<ProductDetail />} />
            <Route path="/audiences" element={<Audiences />} />
            <Route path="/deals" element={<Navigate to="/dashboard" replace />} />
            <Route path="/live-feed" element={<LiveFeed />} />
            <Route path="*" element={<Navigate to="/dashboard" replace />} />
          </Routes>
        </main>
      </div>
    </AppCtx.Provider>
  );
}
