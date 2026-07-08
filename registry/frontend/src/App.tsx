import { useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import Directory from "./pages/Directory";
import Verify from "./pages/Verify";

export default function App() {
  const [registry, setRegistry] = useState("");
  useEffect(() => {
    fetch("/healthz").then((r) => r.json()).then((d) => setRegistry(d.registry ?? "")).catch(() => {});
  }, []);

  return (
    <div className="shell">
      <div className="mock-banner" role="note">
        MOCK REGISTRY
        <span className="mock-banner-sub">demo stand-in — not IAB’s live registry service</span>
      </div>
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">R</span>
          <div className="brand-text">
            <span className="brand-title">AAMP Registry Console <span className="mock-badge" title="A faithful mock of IAB's hosted agent registry — not the live IAB service. It speaks the same contract (discovery + trust→tier) so sellers/buyers integrate identically.">MOCK</span></span>
            <span className="brand-sub">{registry || "agent directory & trust"} · mock registry · powered by Databricks</span>
          </div>
        </div>
        <nav className="mainnav">
          <NavLink to="/directory" className={({ isActive }) => (isActive ? "active" : "")}>Directory</NavLink>
          <NavLink to="/verify" className={({ isActive }) => (isActive ? "active" : "")}>Trust Verify</NavLink>
        </nav>
        <div className="scenario"><span style={{ fontSize: 12, color: "#aeb6bf" }}>Mock registry · not IAB’s live service</span></div>
      </header>
      <main className="content">
        <Routes>
          <Route path="/" element={<Navigate to="/directory" replace />} />
          <Route path="/directory" element={<Directory />} />
          <Route path="/verify" element={<Verify />} />
          <Route path="*" element={<Navigate to="/directory" replace />} />
        </Routes>
      </main>
    </div>
  );
}
