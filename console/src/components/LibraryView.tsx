import { useCallback, useEffect, useState } from "react";
import { fetchMemes, uploadMeme } from "../lib/api";
import { fileToBase64 } from "../lib/files";
import type { LibraryMeme, Meta } from "../types";
import MemeImage from "./MemeImage";

const STATUS_LABEL: Record<string, string> = {
  active: "可檢索",
  pending_review: "待複核",
  removed: "已下架",
};

/** 梗圖庫瀏覽 + 手動上傳（docs/05 §2.2，seed 匯入口）。 */
export default function LibraryView({ meta }: { meta: Meta | null }) {
  const [franchise, setFranchise] = useState("");
  const [category, setCategory] = useState("");
  const [emotion, setEmotion] = useState("");
  const [limit, setLimit] = useState(200);
  const [memes, setMemes] = useState<LibraryMeme[] | null>(null);
  const [selected, setSelected] = useState<LibraryMeme | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);
  const [titleHint, setTitleHint] = useState("");

  const reload = useCallback(() => {
    fetchMemes(
      {
        franchise: franchise || undefined,
        category: category || undefined,
        emotion: emotion || undefined,
      },
      limit,
    )
      .then(setMemes)
      .catch((e) => setNotice(`✕ ${e instanceof Error ? e.message : "載入失敗"}`));
  }, [franchise, category, emotion, limit]);

  // 換篩選條件時把分頁歸零（重新從前 200 張看起）
  const onFilterChange = (setter: (v: string) => void) => (value: string) => {
    setLimit(200);
    setter(value);
  };

  useEffect(reload, [reload]);

  const onUpload = async (file: File | undefined) => {
    if (!file || uploading) return;
    setUploading(true);
    setNotice("上傳並標註中…（約 8–12 秒，完成即可被檢索）");
    try {
      const result = await uploadMeme(await fileToBase64(file), titleHint.trim());
      const review = result.meme_status === "pending_review" ? "（低信心，已轉人工複核）" : "";
      setNotice(
        `✓ 已入庫並標註：「${result.annotation?.ocr_text || "(無文字)"}」${review}`,
      );
      reload();
    } catch (e) {
      setNotice(`✕ ${e instanceof Error ? e.message : "上傳失敗"}`);
    } finally {
      setUploading(false);
    }
  };

  const emotions = meta ? [...new Set(memes?.flatMap((m) => m.annotation?.emotions ?? []))] : [];

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 p-4">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <select value={franchise} onChange={(e) => onFilterChange(setFranchise)(e.target.value)}
          className="rounded border border-line bg-raised px-2 py-1">
          <option value="">全部梗圖包</option>
          {meta?.franchises.map((f) => (
            <option key={f.name} value={f.name}>{f.name}（{f.count}）</option>
          ))}
        </select>
        <select value={category} onChange={(e) => onFilterChange(setCategory)(e.target.value)}
          className="rounded border border-line bg-raised px-2 py-1">
          <option value="">全部分類</option>
          {meta?.categories.map((c) => <option key={c} value={c}>{c}</option>)}
        </select>
        <select value={emotion} onChange={(e) => onFilterChange(setEmotion)(e.target.value)}
          className="rounded border border-line bg-raised px-2 py-1">
          <option value="">全部情緒</option>
          {emotions.map((e) => <option key={e} value={e}>{e}</option>)}
        </select>
        <span className="font-mono text-xs text-muted">
          {memes?.length ?? "…"} 張{memes && memes.length === limit ? "＋" : ""}
        </span>

        <span className="ml-auto flex items-center gap-2">
          <input
            value={titleHint}
            onChange={(e) => setTitleHint(e.target.value)}
            placeholder="主題提示（選填，助標註）"
            className="rounded border border-line bg-raised px-2 py-1 text-xs"
          />
          <label
            className={`rounded border px-3 py-1 text-xs ${
              uploading
                ? "cursor-wait border-line text-muted"
                : "cursor-pointer border-amber text-amber hover:bg-amber-soft"
            }`}
          >
            {uploading ? "處理中…" : "＋ 上傳梗圖"}
            <input type="file" accept="image/png,image/jpeg,image/webp" className="hidden"
              disabled={uploading}
              onChange={(e) => { void onUpload(e.target.files?.[0]); e.target.value = ""; }} />
          </label>
        </span>
      </div>
      {notice && <p className="text-xs text-muted">{notice}</p>}

      <div className="grid flex-1 auto-rows-min grid-cols-2 gap-3 overflow-y-auto
                      md:grid-cols-4 xl:grid-cols-6">
        {memes?.map((meme) => (
          <button
            key={meme.meme_id}
            onClick={() => setSelected(meme)}
            className="rounded-lg border border-line bg-panel p-2 text-left hover:border-amber"
          >
            <MemeImage src={meme.image_url} alt={meme.annotation?.ocr_text || meme.meme_id}
              className="mx-auto max-h-28 object-contain" />
            <p className="mt-1.5 truncate text-xs">{meme.annotation?.ocr_text || "（未標註）"}</p>
            <p className="truncate text-[10px] text-muted">
              {meme.annotation?.franchise ?? "—"} ·{" "}
              {meme.status !== "active" ? STATUS_LABEL[meme.status] : meme.annotation?.emotions.join("、")}
            </p>
          </button>
        ))}
      </div>

      {memes && memes.length === limit && (
        <button
          onClick={() => setLimit((n) => n + 300)}
          className="mx-auto rounded-full border border-line px-5 py-1.5 text-xs text-muted
                     hover:border-amber hover:text-amber"
        >
          載入更多（目前 {limit} 張）
        </button>
      )}

      {selected && (
        <div
          className="fixed inset-0 z-10 flex items-center justify-center bg-ink/80 p-6"
          onClick={() => setSelected(null)}
        >
          <div
            className="flex max-h-full w-full max-w-2xl gap-4 overflow-y-auto rounded-lg
                       border border-line bg-panel p-5"
            onClick={(e) => e.stopPropagation()}
          >
            <MemeImage src={selected.image_url} alt="" className="max-h-72 max-w-60 object-contain" />
            <div className="min-w-0 flex-1 space-y-1.5 text-sm">
              <p className="font-mono text-xs text-muted">
                {selected.meme_id} · {STATUS_LABEL[selected.status]} · 熱度 {selected.hotness}
              </p>
              {selected.annotation ? (
                <>
                  <p><span className="text-muted">圖中文字：</span>{selected.annotation.ocr_text || "（無）"}</p>
                  <p><span className="text-muted">畫面：</span>{selected.annotation.description}</p>
                  <p><span className="text-muted">出處：</span>{selected.annotation.franchise ?? "—"}
                    <span className="ml-3 text-muted">模板：</span>{selected.annotation.template_name ?? "—"}</p>
                  <p><span className="text-muted">情緒：</span>{selected.annotation.emotions.join("、")}</p>
                  {selected.annotation.usage_hints.map((hint) => (
                    <p key={hint}><span className="text-muted">用途：</span>{hint}</p>
                  ))}
                  <p className="font-mono text-xs text-muted">
                    conf={selected.annotation.confidence} · {selected.annotation.model_version}
                  </p>
                </>
              ) : (
                <p className="text-muted">尚未標註——執行 python -m memeradar.understanding.annotator</p>
              )}
              <button onClick={() => setSelected(null)}
                className="mt-2 rounded border border-line px-3 py-1 text-xs text-muted hover:text-fg">
                關閉
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
