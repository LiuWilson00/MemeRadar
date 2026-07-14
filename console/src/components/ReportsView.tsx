import { Flag, Loader2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { fetchReports, resolveReport } from "../lib/api";
import type { ReportedMeme } from "../types";
import MemeImage from "./MemeImage";

/** 檢舉待辦：前台使用者檢舉的梗圖，管理員決定下架或忽略。 */
export default function ReportsView() {
  const [items, setItems] = useState<ReportedMeme[] | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(() => {
    fetchReports()
      .then(setItems)
      .catch(() => setItems([]));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const act = async (memeId: string, action: "remove" | "dismiss") => {
    setBusy(memeId);
    try {
      await resolveReport(memeId, action);
      load();
    } finally {
      setBusy(null);
    }
  };

  if (items === null) {
    return (
      <div className="flex justify-center p-10">
        <Loader2 className="size-6 animate-spin text-muted" />
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 p-10 text-center text-muted">
        <Flag className="size-8" strokeWidth={1.5} />
        <p className="text-sm">目前沒有被檢舉的梗圖</p>
      </div>
    );
  }

  return (
    <div className="space-y-3 animate-fade-in">
      <p className="text-xs text-muted">
        前台使用者檢舉的梗圖，依檢舉人數排序。下架＝從推薦池移除；忽略＝保留但清出清單。
      </p>
      {items.map((r) => (
        <article key={r.meme_id} className="flex gap-4 rounded-lg border border-line bg-panel p-4">
          <MemeImage
            src={`/memes/${r.meme_id}/image`}
            alt=""
            className="max-h-40 max-w-36 self-start object-contain"
          />
          <div className="min-w-0 flex-1 space-y-1 text-sm">
            <p className="flex items-center gap-2">
              <span className="rounded-full bg-danger/15 px-2 py-0.5 text-xs text-danger">
                {r.reports} 人檢舉
              </span>
              <span className="rounded-full border border-line px-2 py-0.5 text-xs text-muted">
                {r.status}
              </span>
            </p>
            <p className="truncate text-fg">{r.ocr_text?.trim() || "（無文字）"}</p>
            {r.franchise && <p className="text-xs text-muted">{r.franchise}</p>}
            <p className="font-mono text-xs text-muted">最後檢舉：{r.last_reported}</p>
            <div className="flex gap-2 pt-1">
              <button
                disabled={busy === r.meme_id}
                onClick={() => act(r.meme_id, "remove")}
                className="rounded border border-line px-3 py-1 text-sm hover:border-danger
                           disabled:opacity-40"
              >
                下架
              </button>
              <button
                disabled={busy === r.meme_id}
                onClick={() => act(r.meme_id, "dismiss")}
                className="rounded border border-line px-3 py-1 text-sm hover:border-signal
                           disabled:opacity-40"
              >
                忽略（保留）
              </button>
            </div>
          </div>
        </article>
      ))}
    </div>
  );
}
