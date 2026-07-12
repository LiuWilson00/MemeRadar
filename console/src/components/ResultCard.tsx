import { useState } from "react";
import { sendFeedback } from "../lib/api";
import type { ResultItem } from "../types";
import MemeImage from "./MemeImage";

function ScoreMeter({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex items-center gap-1.5" title={`${label} = ${value.toFixed(3)}`}>
      <span className="w-11 font-mono text-[10px] text-muted">{label}</span>
      <div className="h-1 flex-1 overflow-hidden rounded bg-raised">
        <div
          className="h-full rounded bg-amber"
          style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }}
        />
      </div>
      <span className="w-10 text-right font-mono text-[10px]">{value.toFixed(2)}</span>
    </div>
  );
}

export default function ResultCard({ item, queryId }: { item: ResultItem; queryId: string }) {
  const [note, setNote] = useState("");
  const [noteOpen, setNoteOpen] = useState(false);
  const [sent, setSent] = useState<"up" | "down" | null>(null);
  const [error, setError] = useState<string | null>(null);

  const rate = async (rating: "up" | "down") => {
    try {
      setError(null);
      await sendFeedback({
        query_id: queryId,
        meme_id: item.meme_id,
        rank: item.rank,
        rating,
        note: note.trim() || null,
      });
      setSent(rating);
    } catch (e) {
      setError(e instanceof Error ? e.message : "回饋送出失敗");
    }
  };

  return (
    <article className="flex flex-col overflow-hidden rounded-lg border border-line bg-panel">
      <div className="relative bg-ink">
        <MemeImage
          src={item.image_url}
          href={item.image_url}
          alt={`推薦梗圖第 ${item.rank} 名`}
          className="mx-auto max-h-48 object-contain"
        />
        <span className="absolute left-2 top-2 rounded bg-ink/80 px-1.5 font-mono text-sm text-amber">
          #{item.rank}
        </span>
      </div>

      <div className="flex flex-1 flex-col gap-2 p-3">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="rounded border border-amber px-1.5 py-0.5 text-xs text-amber">
            {item.matched_strategy}
          </span>
          {item.matched_tags.map((tag) => (
            <span key={tag} className="rounded bg-raised px-1.5 py-0.5 text-xs text-muted">
              {tag}
            </span>
          ))}
        </div>

        <p className="text-sm leading-relaxed">{item.reason}</p>

        <div className="space-y-1">
          <ScoreMeter label="vector" value={item.scores.vector} />
          <ScoreMeter label="rerank" value={item.scores.rerank} />
          <ScoreMeter label="final" value={item.scores.final} />
        </div>

        <div className="mt-auto border-t border-line pt-2">
          {sent ? (
            <p className="text-xs text-signal">已記錄 {sent === "up" ? "👍" : "👎"} ✓</p>
          ) : (
            <div className="space-y-1.5">
              <div className="flex items-center gap-1.5">
                <button
                  onClick={() => rate("up")}
                  className="flex-1 rounded border border-line py-1 hover:border-signal hover:text-signal"
                  aria-label="好推薦"
                >
                  👍
                </button>
                <button
                  onClick={() => rate("down")}
                  className="flex-1 rounded border border-line py-1 hover:border-danger hover:text-danger"
                  aria-label="爛推薦"
                >
                  👎
                </button>
                <button
                  onClick={() => setNoteOpen(!noteOpen)}
                  className="rounded border border-line px-2 py-1 text-xs text-muted hover:text-fg"
                  title="先寫備註再按讚/倒讚，會一起送出"
                >
                  ✎
                </button>
              </div>
              {noteOpen && (
                <input
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  placeholder="備註（選填）——按 👍/👎 時一起送出"
                  className="w-full rounded border border-line bg-raised px-2 py-1 text-xs"
                />
              )}
              {error && <p className="text-xs text-danger">{error}</p>}
            </div>
          )}
        </div>
      </div>
    </article>
  );
}
