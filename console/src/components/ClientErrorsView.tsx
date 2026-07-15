import { AlertTriangle, Loader2, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { fetchClientErrors } from "../lib/api";
import type { ClientError } from "../types";

/** 前台 + 伺服器錯誤（類 CloudWatch）：瀏覽器端錯誤與後端未攔截的 500，方便 debug。 */
export default function ClientErrorsView() {
  const [items, setItems] = useState<ClientError[] | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);

  const load = useCallback(() => {
    fetchClientErrors(200)
      .then(setItems)
      .catch(() => setItems([]));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  if (items === null) {
    return (
      <div className="flex justify-center p-10">
        <Loader2 className="size-6 animate-spin text-muted" />
      </div>
    );
  }

  return (
    <div className="space-y-3 animate-fade-in">
      <div className="flex items-center justify-between">
        <p className="text-xs text-muted">
          瀏覽器端錯誤 +{" "}
          <span className="text-amber">伺服器 500</span>
          （新到舊；後端 500 帶完整 traceback）。
        </p>
        <button
          onClick={load}
          className="flex items-center gap-1 rounded border border-line px-3 py-1 text-xs text-muted hover:text-fg"
        >
          <RefreshCw className="size-3.5" /> 重整
        </button>
      </div>

      {items.length === 0 ? (
        <div className="flex flex-col items-center gap-2 p-10 text-center text-muted">
          <AlertTriangle className="size-8" strokeWidth={1.5} />
          <p className="text-sm">目前沒有前台錯誤 🎉</p>
        </div>
      ) : (
        items.map((e) => {
          const isServer = e.client_id === "__server__";
          return (
          <article
            key={e.error_id}
            className={`rounded-lg border bg-panel p-3 ${
              isServer ? "border-amber/60" : "border-line"
            }`}
          >
            <div className="flex items-start gap-2">
              <AlertTriangle
                className={`mt-0.5 size-4 shrink-0 ${isServer ? "text-amber" : "text-danger"}`}
              />
              {isServer && (
                <span className="mt-0.5 shrink-0 rounded bg-amber/15 px-1.5 py-0.5 text-[10px] font-semibold text-amber">
                  伺服器
                </span>
              )}
              <span className="break-all font-mono text-sm text-fg">{e.message}</span>
            </div>
            <p className="mt-1 font-mono text-xs text-muted">
              {e.created_at} · <span className="text-fg">{e.url || "（無 URL）"}</span> ·{" "}
              {isServer ? "後端" : e.client_id || "匿名"}
            </p>
            {e.user_agent && (
              <p className="mt-0.5 truncate text-[11px] text-muted" title={e.user_agent}>
                {e.user_agent}
              </p>
            )}
            {e.stack && (
              <div className="mt-1.5">
                <button
                  onClick={() => setExpanded(expanded === e.error_id ? null : e.error_id)}
                  className="text-xs text-muted hover:text-fg"
                >
                  {expanded === e.error_id ? "收起堆疊" : "展開堆疊"}
                </button>
                {expanded === e.error_id && (
                  <pre className="mt-1 max-h-64 overflow-auto rounded bg-ink p-2 text-[11px] leading-relaxed text-muted">
                    {e.stack}
                  </pre>
                )}
              </div>
            )}
          </article>
          );
        })
      )}
    </div>
  );
}
