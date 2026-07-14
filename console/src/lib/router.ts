import { useEffect, useState } from "react";

/** 極輕量 client-side routing：讀 pathname、pushState 導航（無外部依賴）。
 * 前台 = "/"，後台 = "/admin/<tab>"。靜態主機以 SPA fallback 回 index.html 即可。
 */
export function useRoute(): string {
  const [path, setPath] = useState(() => window.location.pathname);
  useEffect(() => {
    const onPop = () => setPath(window.location.pathname);
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  return path;
}

export function navigate(to: string): void {
  if (to === window.location.pathname) return;
  window.history.pushState({}, "", to);
  // pushState 不會觸發 popstate，手動派發讓 useRoute 更新
  window.dispatchEvent(new PopStateEvent("popstate"));
}
