import { useState } from "react";
import { Lock, PlayCircle, ShieldCheck } from "lucide-react";
import { api, setDemo, setToken } from "@/lib/api";
import { useStore } from "@/lib/store";
import type { Me } from "@/lib/types";
import { mock } from "@/mock/world";
import { HexMap } from "@/components/HexMap";

export default function Login() {
  const setMe = useStore((s) => s.setMe);
  const [email, setEmail] = useState("admin@acme.test");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      setDemo(false);
      const r = await api<{ token: string; user: Me }>("/api/auth/login", { method: "POST", body: { email, password } });
      setToken(r.token);
      setMe(await api<Me>("/api/me"));
    } catch (x) {
      setErr((x as Error).message || "login failed");
    } finally {
      setBusy(false);
    }
  };
  const demo = () => {
    setDemo(true);
    setMe(mock.me() as Me);
  };

  return (
    <div className="flex h-full bg-nav">
      <div className="relative hidden flex-1 overflow-hidden lg:block">
        <div className="absolute inset-0 opacity-90" style={{ background: "radial-gradient(1200px 600px at 20% 10%, rgba(20,184,166,.25), transparent), radial-gradient(900px 500px at 90% 90%, rgba(91,108,240,.25), transparent)" }} />
        <div className="relative flex h-full flex-col justify-between p-12 text-white">
          <div className="flex items-center gap-3">
            <svg width="34" height="34" viewBox="0 0 32 32">
              <rect width="32" height="32" rx="8" fill="#0d9488" />
              <path d="M6 17h20" stroke="white" strokeWidth="3" strokeLinecap="round" />
              <path d="M16 7v18" stroke="#fbbf24" strokeWidth="3" strokeLinecap="round" />
            </svg>
            <span className="text-xl font-bold tracking-tight">TrustLine</span>
          </div>
          <div className="max-w-xl">
            <h1 className="text-[40px] leading-[1.1] font-bold tracking-tight">
              See everything.
              <br />
              <span className="text-accent-2">Ship nothing sensitive.</span>
            </h1>
            <p className="mt-4 text-[15px] text-white/70">
              Real-time health, security and AIOps for your sites, with raw logs, secrets and real names staying on-site. Only redacted, pseudonymous
              metadata crosses the trust line, over mutual TLS, into your tenant's home region.
            </p>
            <div className="mt-8 rounded-xl bg-white/[0.04] p-4 ring-1 ring-white/10">
              <HexMap sources={mock.sources().sources} groupBy="role" size="sm" dark />
            </div>
          </div>
          <div className="flex gap-6 text-[12px] text-white/50">
            <span className="inline-flex items-center gap-1.5"><ShieldCheck size={14} /> metadata-only egress</span>
            <span className="inline-flex items-center gap-1.5"><Lock size={14} /> mTLS 1.3 · row-level security</span>
          </div>
        </div>
      </div>
      <div className="flex w-full items-center justify-center bg-bg p-6 lg:w-[460px]">
        <form onSubmit={submit} className="w-full max-w-sm space-y-4">
          <div>
            <h2 className="text-[22px] font-bold">Sign in</h2>
            <p className="text-t3">Demo users share the DEMO_PASSWORD from your .env</p>
          </div>
          <label className="block space-y-1">
            <span className="text-[12px] font-medium text-t2">Email</span>
            <input className="input w-full" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" />
          </label>
          <label className="block space-y-1">
            <span className="text-[12px] font-medium text-t2">Password</span>
            <input className="input w-full" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" autoFocus />
          </label>
          {err && <div className="rounded-md bg-poor/10 px-3 py-2 text-[12.5px] text-poor">{err}</div>}
          <button className="btn btn-primary w-full justify-center" disabled={busy}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
          <div className="flex items-center gap-3 text-[11.5px] text-t3">
            <div className="h-px flex-1 bg-line" /> or <div className="h-px flex-1 bg-line" />
          </div>
          <button type="button" onClick={demo} className="btn w-full justify-center">
            <PlayCircle size={15} /> Explore with demo data
          </button>
          <div className="space-y-0.5 text-[11.5px] text-t3">
            <div>admin@acme.test · viewer@acme.test · admin@globex.test</div>
            <div>ops@trustline.test (platform-ops: fleet health only)</div>
          </div>
        </form>
      </div>
    </div>
  );
}
