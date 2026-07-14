import { StrictMode, useEffect } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App";
import { useRoute } from "./lib/router";
import MobileApp from "./mobile/MobileApp";

/** 單一 SPA 入口：路徑決定前台 / 後台（取代原本 index.html + admin.html 雙入口）。
 * "/admin*" → 後台 Console；其餘 → 前台手機 client。 */
function Root() {
  const path = useRoute();
  const isAdmin = path.startsWith("/admin");
  useEffect(() => {
    document.title = isAdmin ? "MemeRadar 後台" : "MemeRadar";
  }, [isAdmin]);
  return isAdmin ? <App /> : <MobileApp />;
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Root />
  </StrictMode>,
);
