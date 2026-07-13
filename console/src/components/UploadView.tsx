import { Ban, Check, CircleDashed, LoaderCircle, X } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { fetchVlmModels, uploadMemeClassified } from "../lib/api";
import { fileToBase64, imageFilesFrom } from "../lib/files";
import { runUploadQueue, type UploadItem, type UploadSummary } from "../lib/uploadQueue";

/** 批次上傳（seed 匯入口）：拖曳一疊圖 → 逐張入庫 → 標註 → 向量化，即時回報。 */

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
  const [items, setItems] = useState<UploadItem[]>([]);
  const [running, setRunning] = useState(false);
  const [summary, setSummary] = useState<UploadSummary | null>(null);
  const [dragging, setDragging] = useState(false);
  const [models, setModels] = useState<string[]>([]);
  const [model, setModel] = useState<string>("");
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    fetchVlmModels()
      .then((r) => {
        setModels(r.models);
        setModel(r.default ?? r.models[0] ?? "");
      })
      .catch(() => {});
  }, []);

  const start = useCallback(
    async (files: File[]) => {
      if (running || files.length === 0) return;
      setRunning(true);
      setSummary(null);
      const hint = titleHint.trim();
      const result = await runUploadQueue(
        files,
        async (file) => uploadMemeClassified(await fileToBase64(file), hint, model),
        setItems,
      );
      setSummary(result);
      setRunning(false);
      onDone?.();
    },
    [running, titleHint, model, onDone],
  );

  const done = items.filter((i) => i.status !== "queued" && i.status !== "uploading").length;

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto p-4">
      <div className="flex flex-wrap items-center gap-2">
        <input
          value={titleHint}
          onChange={(e) => setTitleHint(e.target.value)}
          disabled={running}
          placeholder="主題提示（選填）——例如「海綿寶寶」，整批共用，餵給標註當上下文"
          className="min-w-56 flex-1 rounded border border-line bg-raised px-3 py-1.5 text-sm
                     disabled:opacity-50"
        />
        <label className="flex items-center gap-1.5 text-xs text-muted">
          標註模型
          <select
            value={model}
            onChange={(e) => setModel(e.target.value)}
            disabled={running || models.length === 0}
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
        disabled={running}
        onClick={() => inputRef.current?.click()}
        onDragOver={(e) => {
          e.preventDefault();
          if (!running) setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          void start(imageFilesFrom(e.dataTransfer.files));
        }}
        className={`flex flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed
                    px-6 py-12 text-center transition-colors ${
                      dragging
                        ? "border-amber bg-amber-soft"
                        : "border-line hover:border-amber/60"
                    } ${running ? "cursor-wait opacity-60" : "cursor-pointer"}`}
      >
        <div className={`radar h-16 w-16 ${running ? "" : "opacity-40"}`} />
        <p className="text-sm">
          {running ? (
            <span className="text-amber">處理中… {done}/{items.length}</span>
          ) : (
            <>把梗圖<span className="text-amber">拖曳到這裡</span>，或點擊選檔（可多選）</>
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
            void start(imageFilesFrom(e.target.files));
            e.target.value = "";
          }}
        />
      </button>

      {summary && (
        <div className="rounded border border-line bg-panel px-4 py-3 text-sm">
          <p className="font-semibold">
            這批完成：
            <span className="ml-2 text-chart-up">入庫 {summary.done}</span>
            <span className="ml-3 text-muted">重複 {summary.duplicate}</span>
            <span className="ml-3 text-danger">失敗 {summary.error}</span>
          </p>
          {summary.pendingReview > 0 && (
            <p className="mt-1 text-xs text-amber">
              其中 {summary.pendingReview} 張標註信心偏低，已轉「複核」頁待審。
            </p>
          )}
          <p className="mt-1 text-xs text-muted">
            到「梗圖庫」查看新入庫的圖，或在終端機跑 <code>python -m memeradar.ingestion.coverage</code>{" "}
            看策略配平還缺哪些。
          </p>
        </div>
      )}

      {items.length > 0 && (
        <ul className="space-y-1 font-mono text-xs">
          {items.map((item, idx) => {
            const s = STATUS[item.status];
            return (
              <li
                key={`${item.name}-${idx}`}
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
