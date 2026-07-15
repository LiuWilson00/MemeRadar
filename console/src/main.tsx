import { GoogleOAuthProvider } from "@react-oauth/google";
import { StrictMode, useEffect } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App";
import ErrorBoundary from "./components/ErrorBoundary";
import { GOOGLE_CLIENT_ID } from "./lib/auth";
import { useRoute } from "./lib/router";
import MobileApp from "./mobile/MobileApp";

/** 單一 SPA 入口：路徑決定前台 / 後台（取代原本 index.html + admin.html 雙入口）。
 * "/admin*" → 後台 Console；其餘 → 前台手機 client（含 Google 登入）。 */
function Root() {
  const path = useRoute();
  const isAdmin = path.startsWith("/admin");
  useEffect(() => {
    document.title = isAdmin ? "MemeRadar 後台" : "MemeRadar";
  }, [isAdmin]);
  if (isAdmin) return <App />;
  // 設了 Client ID 才掛 Google 登入 provider；沒設（本地未設定）也能正常跑，只是不顯示登入。
  return GOOGLE_CLIENT_ID ? (
    <GoogleOAuthProvider clientId={GOOGLE_CLIENT_ID}>
      <MobileApp />
    </GoogleOAuthProvider>
  ) : (
    <MobileApp />
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ErrorBoundary>
      <Root />
    </ErrorBoundary>
  </StrictMode>,
);
