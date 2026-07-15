import { GoogleOAuthProvider } from "@react-oauth/google";
import { StrictMode, useEffect } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App";
import ErrorBoundary from "./components/ErrorBoundary";
import { reportClientError } from "./lib/api";
import { logBreadcrumb } from "./lib/breadcrumbs";
import { GOOGLE_CLIENT_ID } from "./lib/auth";

// 全域捕捉未攔截的錯誤 / promise rejection → 回報後台 + 留麵包屑（best-effort、去重、限量）
if (typeof window !== "undefined") {
  window.addEventListener("error", (e) => {
    logBreadcrumb("error", (e.message || "window error").slice(0, 120));
    reportClientError(e.message || "window error", { stack: e.error?.stack, url: e.filename });
  });
  window.addEventListener("unhandledrejection", (e) => {
    const r = e.reason as { message?: string; stack?: string } | undefined;
    const msg = r?.message || String(r ?? "unhandledrejection");
    logBreadcrumb("error", msg.slice(0, 120));
    reportClientError(msg, { stack: r?.stack });
  });
}
import { useRoute } from "./lib/router";
import MobileApp from "./mobile/MobileApp";

/** 單一 SPA 入口：路徑決定前台 / 後台（取代原本 index.html + admin.html 雙入口）。
 * "/admin*" → 後台 Console；其餘 → 前台手機 client（含 Google 登入）。 */
function Root() {
  const path = useRoute();
  const isAdmin = path.startsWith("/admin");
  const shareMatch = path.match(/^\/m\/([^/]+)$/); // 分享 deep-link /m/{id}
  useEffect(() => {
    document.title = isAdmin ? "MemeRadar 後台" : "MemeRadar";
  }, [isAdmin]);
  if (isAdmin) return <App />;
  // 設了 Client ID 才掛 Google 登入 provider；沒設（本地未設定）也能正常跑，只是不顯示登入。
  const app = <MobileApp initialMemeId={shareMatch?.[1] ?? null} />;
  return GOOGLE_CLIENT_ID ? (
    <GoogleOAuthProvider clientId={GOOGLE_CLIENT_ID}>{app}</GoogleOAuthProvider>
  ) : (
    app
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary>
      <Root />
    </ErrorBoundary>
  </StrictMode>,
);
