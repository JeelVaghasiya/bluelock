import { StrictMode, Suspense, lazy, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "sonner";
import "./styles/index.css";
import { api, getToken, isMock } from "./lib/api";
import { useStore } from "./lib/store";
import type { Me } from "./lib/types";
import { Shell } from "./components/Shell";
import { Skeleton } from "./components/ui";
import Login from "./pages/Login";
import Overview from "./pages/Overview";

const Infrastructure = lazy(() => import("./pages/Infrastructure"));
const Metrics = lazy(() => import("./pages/Metrics"));
const ServiceMap = lazy(() => import("./pages/ServiceMap"));
const Events = lazy(() => import("./pages/Events"));
const Security = lazy(() => import("./pages/Security"));
const Incidents = lazy(() => import("./pages/Incidents"));
const IncidentPage = lazy(() => import("./pages/IncidentDetail"));
const Fleet = lazy(() => import("./pages/Fleet"));
const Trust = lazy(() => import("./pages/Trust"));
const SloPage = lazy(() => import("./pages/Slo"));
const Audit = lazy(() => import("./pages/Audit"));
const Settings = lazy(() => import("./pages/Settings"));

const qc = new QueryClient({
  defaultOptions: { queries: { staleTime: 2000, retry: 1, refetchOnWindowFocus: false, placeholderData: (prev: unknown) => prev } },
});

function App() {
  const me = useStore((s) => s.me);
  const setMe = useStore((s) => s.setMe);
  const theme = useStore((s) => s.theme);
  const [checked, setChecked] = useState(false);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  useEffect(() => {
    const out = () => {
      setMe(null);
      qc.clear();
    };
    window.addEventListener("tl-logout", out);
    if (getToken() || isMock()) {
      api<Me>("/api/me")
        .then(setMe)
        .catch(() => setMe(null))
        .finally(() => setChecked(true));
    } else setChecked(true);
    return () => window.removeEventListener("tl-logout", out);
  }, [setMe]);

  if (!checked) return null;
  if (!me) return <Login />;
  if (me.role === "platform-ops") {
    return (
      <Shell>
        <Suspense fallback={<div className="p-4"><Skeleton h={400} /></div>}>
          <Fleet />
        </Suspense>
      </Shell>
    );
  }
  return (
    <Shell>
      <Suspense fallback={<div className="p-4"><Skeleton h={400} /></div>}>
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/infrastructure" element={<Infrastructure />} />
          <Route path="/metrics" element={<Metrics />} />
          <Route path="/network" element={<ServiceMap />} />
          <Route path="/events" element={<Events />} />
          <Route path="/security" element={<Security />} />
          <Route path="/incidents" element={<Incidents />} />
          <Route path="/incidents/:id" element={<IncidentPage />} />
          <Route path="/fleet" element={<Fleet />} />
          <Route path="/trust" element={<Trust />} />
          <Route path="/slo" element={<SloPage />} />
          <Route path="/audit" element={<Audit />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="*" element={<Overview />} />
        </Routes>
      </Suspense>
    </Shell>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={qc}>
      <BrowserRouter>
        <App />
        <Toaster position="bottom-right" richColors closeButton />
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
