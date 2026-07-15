import { Loader2, MessageCircle, RefreshCw, ThumbsDown, ThumbsUp } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { fetchChatFeedback } from "../lib/api";
import type { ChatFeedbackRow } from "../types";
import MemeImage from "./MemeImage";

/** 梗友回饋：使用者對「只會回梗圖的朋友」每則回覆的 👍/👎，供優化選圖。 */
export default function ChatFeedbackView() {
  const [items, setItems] = useState<ChatFeedbackRow[] | null>(null);
  const [filter, setFilter] = useState<"all" | "up" | "down">("all");

  const load = useCallback(() => {
    fetchChatFeedback(300)
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

  const ups = items.filter((i) => i.rating === "up").length;
  const downs = items.filter((i) => i.rating === "down").length;
  const shown = filter === "all" ? items : items.filter((i) => i.rating === filter);

  return (
    <div className="space-y-3 animate-fade-in">
      <div className="flex flex-wrap items-center gap-3 text-xs">
        <p className="text-muted">梗友回覆評價（訊息 → 回哪張圖 → 讚/倒讚），優化選圖用。</p>
        <span className="text-chart-up">👍 {ups}</span>
        <span className="text-danger">👎 {downs}</span>
        <div className="ml-auto flex items-center gap-1">
          {(["all", "up", "down"] as const).map((f) => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`rounded px-2 py-1 ${
                filter === f ? "bg-amber text-ink" : "border border-line text-muted"
              }`}
            >
              {f === "all" ? "全部" : f === "up" ? "👍" : "👎"}
            </button>
          ))}
          <button
            onClick={load}
            className="flex items-center gap-1 rounded border border-line px-2 py-1 text-muted hover:text-fg"
          >
            <RefreshCw className="size-3.5" /> 重整
          </button>
        </div>
      </div>

      {shown.length === 0 ? (
        <div className="flex flex-col items-center gap-2 p-10 text-center text-muted">
          <MessageCircle className="size-8" strokeWidth={1.5} />
          <p className="text-sm">目前沒有回饋</p>
        </div>
      ) : (
        shown.map((r) => (
          <article key={r.event_id} className="flex gap-3 rounded-lg border border-line bg-panel p-3">
            <MemeImage
              src={`/memes/${r.meme_id}/image`}
              alt=""
              className="max-h-24 max-w-24 self-start rounded object-contain"
            />
            <div className="min-w-0 flex-1 text-sm">
              <p className="flex items-center gap-1.5">
                {r.rating === "up" ? (
                  <ThumbsUp className="size-4 text-signal" strokeWidth={2.2} />
                ) : (
                  <ThumbsDown className="size-4 text-danger" strokeWidth={2.2} />
                )}
                <span className="font-mono text-xs text-muted">{r.created_at}</span>
              </p>
              <p className="mt-1 break-words text-fg">
                <span className="text-muted">訊息：</span>
                {r.message?.trim() || "（無）"}
              </p>
              <p className="mt-0.5 truncate text-xs text-muted">
                回了：{r.ocr_text?.trim() || r.franchise || r.meme_id}
              </p>
            </div>
          </article>
        ))
      )}
    </div>
  );
}
