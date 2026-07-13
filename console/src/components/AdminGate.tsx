import type { FormEvent, ReactNode } from "react";
import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "../lib/api";

const AUTH_KEY = "memeradar.adminAuth";

/** 登出：清掉憑證並重載。供後台 header 呼叫。 */
export function logout() {
  sessionStorage.removeItem(AUTH_KEY);
  location.reload();
}

type State = "checking" | "open" | "login";

/** 後台登入閘門：探測 admin 端點決定是否需要登入。
 * - 200：不設防（本機開發）或已登入 → 直接進 Console
 * - 401：需要登入 → 顯示登入頁
 * - 其他 / 網路錯誤：放行，讓 Console 自行顯示錯誤（避免因後端暫時異常把人鎖在外）
 */
export default function AdminGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<State>("checking");

  const probe = useCallback(async () => {
    try {
      const r = await apiFetch("/vlm/usage"); // 後台限定端點；帶著已存憑證探測
      setState(r.status === 401 ? "login" : "open");
    } catch {
      setState("open");
    }
  }, []);

  useEffect(() => {
    void probe();
  }, [probe]);

  if (state === "checking") {
    return <div className="grid h-screen place-items-center text-sm text-muted">檢查登入中…</div>;
  }
  if (state === "login") {
    return <LoginForm onDone={() => void probe()} />;
  }
  return <>{children}</>;
}

function LoginForm({ onDone }: { onDone: () => void }) {
  const [user, setUser] = useState("");
  const [pass, setPass] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr("");
    sessionStorage.setItem(AUTH_KEY, btoa(`${user}:${pass}`));
    try {
      const r = await apiFetch("/vlm/usage");
      if (r.ok) {
        onDone();
        return;
      }
      sessionStorage.removeItem(AUTH_KEY);
      setErr(r.status === 401 ? "帳號或密碼錯誤" : `登入失敗（${r.status}）`);
    } catch {
      sessionStorage.removeItem(AUTH_KEY);
      setErr("無法連線到伺服器");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid h-screen place-items-center bg-ink px-4">
      <form onSubmit={submit} className="w-72 rounded-lg border border-line bg-panel p-6">
        <h1 className="mb-1 font-mono text-sm font-semibold tracking-[0.3em]">
          MEME<span className="text-amber">RADAR</span>
        </h1>
        <p className="mb-5 text-xs text-muted">後台管理登入</p>
        <input
          value={user}
          onChange={(e) => setUser(e.target.value)}
          placeholder="帳號"
          autoFocus
          className="mb-2 w-full rounded border border-line bg-raised px-3 py-2 text-sm outline-none focus:border-amber"
        />
        <input
          value={pass}
          onChange={(e) => setPass(e.target.value)}
          type="password"
          placeholder="密碼"
          className="mb-3 w-full rounded border border-line bg-raised px-3 py-2 text-sm outline-none focus:border-amber"
        />
        {err && <p className="mb-3 text-xs text-danger">{err}</p>}
        <button
          type="submit"
          disabled={busy || !user}
          className="w-full rounded bg-amber py-2 text-sm font-semibold text-ink disabled:opacity-40"
        >
          {busy ? "登入中…" : "登入"}
        </button>
      </form>
    </div>
  );
}
