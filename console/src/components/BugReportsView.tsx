import { Bug, Loader2, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { fetchBugReports } from "../lib/api";
import type { BugReport } from "../types";

/** 後台：使用者主動回報的問題（描述 + 操作麵包屑時間軸 + 裝置資訊）。 */
export default function BugReportsView() {
  const [items, setItems] = useState<BugReport[] | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);

  const load = useCallback(() => {
    fetchBugReports(200)
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
          使用者主動回報的問題（新到舊）；展開看送出前的操作麵包屑時間軸，方便重現。
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
          <Bug className="size-8" strokeWidth={1.5} />
          <p className="text-sm">目前沒有問題回報 🎉</p>
        </div>
      ) : (
        items.map((r) => {
          const vw = Number(r.meta?.vw);
          const vh = Number(r.meta?.vh);
          return (
            <article key={r.report_id} className="rounded-lg border border-line bg-panel p-3">
              <div className="flex items-start gap-2">
                <Bug className="mt-0.5 size-4 shrink-0 text-amber" />
                <p className="break-words text-sm text-fg">{r.description}</p>
              </div>
              <p className="mt-1 font-mono text-xs text-muted">
                {r.created_at} · <span className="text-fg">{r.url || "?"}</span> ·{" "}
                {r.client_id || "匿名"}
                {vw ? ` · ${vw}×${vh}` : ""}
              </p>
              {r.user_agent && (
                <p className="mt-0.5 truncate text-[11px] text-muted" title={r.user_agent}>
                  {r.user_agent}
                </p>
              )}
              {r.breadcrumbs.length > 0 && (
                <div className="mt-2">
                  <button
                    onClick={() => setExpanded(expanded === r.report_id ? null : r.report_id)}
                    className="text-xs text-amber hover:underline"
                  >
                    {expanded === r.report_id
                      ? "收起操作紀錄"
                      : `操作紀錄（${r.breadcrumbs.length} 筆）`}
                  </button>
                  {expanded === r.report_id && (
                    <ol className="mt-1.5 max-h-72 space-y-1 overflow-auto border-l border-line pl-3">
                      {r.breadcrumbs.map((c, i) => (
                        <li key={i} className="text-xs leading-relaxed">
                          <span className="font-mono text-muted">+{(c.t / 1000).toFixed(1)}s</span>{" "}
                          <span className="rounded bg-raised px-1 text-[10px] text-muted">
                            {c.type}
                          </span>{" "}
                          <span className="text-fg">{c.msg}</span>
                          {c.data && (
                            <span className="font-mono text-[11px] text-muted">
                              {" "}
                              {JSON.stringify(c.data)}
                            </span>
                          )}
                        </li>
                      ))}
                    </ol>
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
