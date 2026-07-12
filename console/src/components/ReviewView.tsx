import { useCallback, useEffect, useState } from "react";
import { fetchDedupReviews, fetchMemes, resolveDedup, reviewAnnotation } from "../lib/api";
import type { DedupReviewItem, LibraryMeme, Meta } from "../types";
import MemeImage from "./MemeImage";

function toggle(list: string[], value: string): string[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
}

/** 標註待審卡片：可編修標籤後通過 / 淘汰。 */
function AnnotationCard({
  meme,
  meta,
  onDone,
}: {
  meme: LibraryMeme;
  meta: Meta | null;
  onDone: () => void;
}) {
  const ann = meme.annotation;
  const [ocr, setOcr] = useState(ann?.ocr_text ?? "");
  const [franchise, setFranchise] = useState(ann?.franchise ?? "");
  const [emotions, setEmotions] = useState<string[]>(ann?.emotions ?? []);
  const [hints, setHints] = useState((ann?.usage_hints ?? []).join("\n"));
  const [isMeme, setIsMeme] = useState(ann?.is_meme ?? true);
  const [nsfw, setNsfw] = useState(ann?.nsfw ?? false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const act = async (action: "approve" | "remove") => {
    setBusy(true);
    setError(null);
    try {
      const patch =
        action === "approve" && ann
          ? {
              ocr_text: ocr,
              franchise: franchise.trim() || null,
              emotions,
              usage_hints: hints.split("\n").map((h) => h.trim()).filter(Boolean),
              is_meme: isMeme,
              nsfw,
            }
          : undefined;
      await reviewAnnotation(meme.meme_id, action, patch);
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作失敗");
      setBusy(false);
    }
  };

  return (
    <article className="flex gap-4 rounded-lg border border-line bg-panel p-4">
      <MemeImage src={meme.image_url} alt="" className="max-h-52 max-w-44 self-start object-contain" />
      <div className="min-w-0 flex-1 space-y-2 text-sm">
        {ann === null ? (
          <p className="text-muted">此圖尚無標註（可能為模型拒答）——只能通過（原樣）或淘汰</p>
        ) : (
          <>
            <label className="block">
              <span className="text-xs text-muted">圖中文字</span>
              <input value={ocr} onChange={(e) => setOcr(e.target.value)}
                className="w-full rounded border border-line bg-raised px-2 py-1" />
            </label>
            <label className="block">
              <span className="text-xs text-muted">出處（franchise，可空）</span>
              <input value={franchise} onChange={(e) => setFranchise(e.target.value)}
                className="w-full rounded border border-line bg-raised px-2 py-1" />
            </label>
            <div>
              <span className="text-xs text-muted">情緒</span>
              <div className="mt-1 flex flex-wrap gap-1">
                {meta?.emotions.map((emotion) => (
                  <button key={emotion}
                    onClick={() => setEmotions(toggle(emotions, emotion))}
                    className={`rounded-full border px-1.5 py-0.5 text-xs ${
                      emotions.includes(emotion)
                        ? "border-amber bg-amber-soft text-amber"
                        : "border-line text-muted"
                    }`}>
                    {emotion}
                  </button>
                ))}
              </div>
            </div>
            <label className="block">
              <span className="text-xs text-muted">使用情境（每行一條）</span>
              <textarea rows={2} value={hints} onChange={(e) => setHints(e.target.value)}
                className="w-full rounded border border-line bg-raised px-2 py-1" />
            </label>
            <div className="flex gap-4 text-xs">
              <label className="flex items-center gap-1">
                <input type="checkbox" checked={isMeme}
                  onChange={(e) => setIsMeme(e.target.checked)}
                  className="accent-(--color-amber)" />
                是梗圖
              </label>
              <label className="flex items-center gap-1">
                <input type="checkbox" checked={nsfw}
                  onChange={(e) => setNsfw(e.target.checked)}
                  className="accent-(--color-amber)" />
                NSFW
              </label>
              {ann.confidence != null && (
                <span className="font-mono text-muted">原信心 {ann.confidence}</span>
              )}
            </div>
          </>
        )}
        <div className="flex gap-2 pt-1">
          <button disabled={busy} onClick={() => act("approve")}
            className="rounded border border-signal px-3 py-1 text-signal hover:bg-signal/10
                       disabled:opacity-40">
            通過（重建向量）
          </button>
          <button disabled={busy} onClick={() => act("remove")}
            className="rounded border border-danger px-3 py-1 text-danger hover:bg-danger/10
                       disabled:opacity-40">
            淘汰
          </button>
          {error && <span className="self-center text-xs text-danger">{error}</span>}
        </div>
      </div>
    </article>
  );
}

/** 去重裁決卡片：並排比圖，人工判合併或不同梗。 */
function DedupCard({ item, onDone }: { item: DedupReviewItem; onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const act = async (resolution: "merged" | "distinct") => {
    setBusy(true);
    setError(null);
    try {
      await resolveDedup(item.review_id, resolution);
      onDone();
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作失敗");
      setBusy(false);
    }
  };

  const side = (label: string, info: DedupReviewItem["meme"]) => (
    <figure className="flex-1 text-center">
      <MemeImage src={info.image_url} alt="" className="mx-auto max-h-44 object-contain" />
      <figcaption className="mt-1 text-xs">
        <span className="text-muted">{label}：</span>
        {info.ocr_text || "（無文字）"}
      </figcaption>
    </figure>
  );

  return (
    <article className="rounded-lg border border-line bg-panel p-4">
      <p className="mb-2 font-mono text-xs text-muted">
        {item.layer} 層命中{item.score != null && `，score=${item.score.toFixed(3)}`}
      </p>
      <div className="flex gap-4">
        {side("新圖", item.meme)}
        {side("既有", item.matched)}
      </div>
      <div className="mt-3 flex justify-center gap-2">
        <button disabled={busy} onClick={() => act("merged")}
          className="rounded border border-line px-3 py-1 text-sm hover:border-amber
                     disabled:opacity-40">
          同一張 → 合併
        </button>
        <button disabled={busy} onClick={() => act("distinct")}
          className="rounded border border-line px-3 py-1 text-sm hover:border-signal
                     disabled:opacity-40">
          不同梗（如同模板不同字）→ 都保留
        </button>
        {error && <span className="self-center text-xs text-danger">{error}</span>}
      </div>
    </article>
  );
}

export default function ReviewView({ meta }: { meta: Meta | null }) {
  const [pendingAnnotations, setPendingAnnotations] = useState<LibraryMeme[] | null>(null);
  const [dedupItems, setDedupItems] = useState<DedupReviewItem[] | null>(null);

  const reload = useCallback(() => {
    fetchMemes({ status: "pending_review" }).then(setPendingAnnotations).catch(() => {});
    fetchDedupReviews().then(setDedupItems).catch(() => {});
  }, []);
  useEffect(reload, [reload]);

  return (
    <div className="min-h-0 flex-1 space-y-6 overflow-y-auto p-4">
      <section>
        <h2 className="mb-2 font-mono text-xs tracking-widest text-muted">
          標註待審（{pendingAnnotations?.length ?? "…"}）——低信心 / 非梗圖判定 / 模型拒答
        </h2>
        {pendingAnnotations?.length === 0 && (
          <p className="text-sm text-muted">目前沒有待審標註 ✓</p>
        )}
        <div className="space-y-3">
          {pendingAnnotations?.map((meme) => (
            <AnnotationCard key={meme.meme_id} meme={meme} meta={meta} onDone={reload} />
          ))}
        </div>
      </section>

      <section>
        <h2 className="mb-2 font-mono text-xs tracking-widest text-muted">
          去重裁決（{dedupItems?.length ?? "…"}）——標註後 OCR 無法自動裁決的殘餘
        </h2>
        {dedupItems?.length === 0 && <p className="text-sm text-muted">目前沒有待裁決配對 ✓</p>}
        <div className="grid gap-3 xl:grid-cols-2">
          {dedupItems?.map((item) => (
            <DedupCard key={item.review_id} item={item} onDone={reload} />
          ))}
        </div>
      </section>
    </div>
  );
}
