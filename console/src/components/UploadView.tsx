import { Ban, Check, CircleDashed, LoaderCircle, Trash2, X } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { fetchVlmModels, uploadMemeClassified } from "../lib/api";
import { fileToBase64, imageFilesFrom } from "../lib/files";
import { summarize, useUploadQueue, type UploadItem } from "../lib/uploadQueue";

/** 批次上傳（seed 匯入口）：拖曳一疊圖 → 逐張入庫 → 標註 → 向量化，即時回報。
 * 佇列可持續累加（執行中再拖入接到尾端），已完成的紀錄跨重整保留。 */

const STATUS: Record<
  UploadItem["status"],
  { Icon: LucideIcon; cls: string; label: string; spin?: boolean }
> = {
  queued: { Icon: CircleDashed, cls: "text-muted", label: "排隊中" },
  uploading: { Icon: LoaderCircle, cls: "text-amber", label: "入庫標註中…", spin: true },
  done: { Icon: Check, cls: "text-chart-up", label: "完成" },
  duplicate: { Icon: Ban, cls: "text-muted", label: "已存在" },
  error: { Icon: X, cls: "text-danger", label: "失敗" },
};

export default function UploadView({ onDone }: { onDone?: () => void }) {
  const [titleHint, setTitleHint] = useState("");
  const [dragging, setDragging] = useState(false);
  const [models, setModels] = useState<string[]>([]);
  const [model, setModel] = useState<string>("");
  const inputRef = useRef<HTMLInputElement>(null);

  const uploadOne = useCallback(
    async (file: File) => uploadMemeClassified(await fileToBase64(file), titleHint.trim(), model),
    [titleHint, model],
  );
  const { items, running, add, clear } = useUploadQueue(uploadOne);
  const summary = summarize(items);

  useEffect(() => {
    fetchVlmModels()
      .then((r) => {
        setModels(r.models);
        setModel(r.default ?? r.models[0] ?? "");
      })
      .catch(() => {});
  }, []);

  // 佇列跑完（idle）就刷新一次上層（梗圖庫 / meta 計數）
  const wasRunning = useRef(false);
  useEffect(() => {
    if (wasRunning.current && !running) onDone?.();
    wasRunning.current = running;
  }, [running, onDone]);

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-4">
      <div className="flex flex-wrap items-center gap-2">
        <input
          value={titleHint}
          onChange={(e) => setTitleHint(e.target.value)}
          placeholder="主題提示（選填）——例如「海綿寶寶」，餵給標註當上下文（可隨時改，套用到之後拖入的圖）"
          className="min-w-56 flex-1 rounded border border-line bg-raised px-3 py-1.5 text-sm"
        />
        <label className="flex items-center gap-1.5 text-xs text-muted">
          標註模型
          <select
            value={model}
            onChange={(e) => setModel(e.target.value)}
            disabled={models.length === 0}
            className="rounded border border-line bg-raised px-2 py-1.5 text-xs text-fg
                       disabled:opacity-50"
          >
            {models.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </label>
      </div>

      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          add(imageFilesFrom(e.dataTransfer.files));
        }}
        className={`flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl
                    border-2 border-dashed px-6 py-12 text-center transition-colors ${
                      dragging ? "border-amber bg-amber-soft" : "border-line hover:border-amber/60"
                    }`}
      >
        <div className={`radar h-16 w-16 ${running ? "" : "opacity-40"}`} />
        <p className="text-sm">
          {running ? (
            <span className="text-amber">
              處理中… {summary.total - summary.active}/{summary.total}（可繼續拖入，接到尾端）
            </span>
          ) : (
            <>
              把梗圖<span className="text-amber">拖曳到這裡</span>，或點擊選檔（可多選）
            </>
          )}
        </p>
        <p className="text-xs text-muted">
          支援 PNG / JPEG / WebP · 每張約 8–12 秒（入庫 → 標註 → 向量化）· 重複自動略過
        </p>
        <input
          ref={inputRef}
          type="file"
          accept="image/png,image/jpeg,image/webp"
          multiple
          className="hidden"
          onChange={(e) => {
            add(imageFilesFrom(e.target.files));
            e.target.value = "";
          }}
        />
      </button>

      {items.length > 0 && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded border border-line bg-panel px-4 py-2 text-sm">
          <span className="font-semibold">佇列 {summary.total}</span>
          {summary.active > 0 && <span className="text-amber">進行中 {summary.active}</span>}
          <span className="text-chart-up">入庫 {summary.done}</span>
          <span className="text-muted">重複 {summary.duplicate}</span>
          <span className="text-danger">失敗 {summary.error}</span>
          {summary.pendingReview > 0 && (
            <span className="text-amber">待複核 {summary.pendingReview}</span>
          )}
          <button
            onClick={clear}
            disabled={running}
            title="清除佇列紀錄（不影響已入庫的梗圖）"
            className="ml-auto flex items-center gap-1 text-xs text-muted hover:text-fg disabled:opacity-40"
          >
            <Trash2 className="size-3.5" /> 清除紀錄
          </button>
        </div>
      )}

      {items.length > 0 && (
        <ul className="space-y-1 font-mono text-xs">
          {items.map((item) => {
            const s = STATUS[item.status];
            return (
              <li
                key={item.id}
                className="flex items-center gap-2 rounded border border-line/50 bg-panel/50 px-3 py-1.5"
              >
                <s.Icon
                  className={`size-4 shrink-0 ${s.cls} ${s.spin ? "animate-spin" : ""}`}
                  strokeWidth={2}
                  aria-hidden
                />
                <span className="w-40 shrink-0 truncate text-fg" title={item.name}>
                  {item.name}
                </span>
                <span className={`shrink-0 ${s.cls}`}>{s.label}</span>
                {item.message && (
                  <span className="truncate text-muted" title={item.message}>
                    {item.status === "done" ? "「" + item.message + "」" : item.message}
                    {item.pendingReview && <span className="ml-1 text-amber">· 待複核</span>}
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
