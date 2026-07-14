/** 批次上傳佇列：可持續累加、跨重整保留「已完成」紀錄（seed 匯入口）。
 *
 * 循序處理（非並發）：每張跑「入庫 → 標註 → 向量化」約 8–12 秒，逐張回報。
 * 執行中再拖入新圖 → 接到佇列尾繼續跑（不中斷、不取代舊清單）。
 * 終態（完成/重複/失敗）存 localStorage，重整後仍看得到；排隊/處理中因無 File
 * 位元組無法續跑，故不持久化（重整後未跑完的需重新拖入）。
 */
import { useCallback, useEffect, useRef, useState } from "react";

const STORAGE_KEY = "memeradar.uploadHistory";
const MAX_ITEMS = 300;

export type UploadItemStatus = "queued" | "uploading" | "done" | "duplicate" | "error";

export type UploadOutcome =
  | { kind: "done"; memeId: string; pendingReview: boolean; ocr: string }
  | { kind: "duplicate"; message: string }
  | { kind: "error"; message: string };

export interface UploadItem {
  id: string;
  name: string;
  status: UploadItemStatus;
  message: string;
  pendingReview: boolean;
}

export interface UploadSummary {
  total: number;
  done: number;
  duplicate: number;
  error: number;
  pendingReview: number;
  active: number; // queued + uploading
}

const isTerminal = (s: UploadItemStatus) => s !== "queued" && s !== "uploading";

function loadPersisted(): UploadItem[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as UploadItem[]) : [];
  } catch {
    return [];
  }
}

function persist(items: UploadItem[]): void {
  try {
    const terminal = items.filter((i) => isTerminal(i.status)).slice(-MAX_ITEMS);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(terminal));
  } catch {
    /* localStorage 滿了 / 停用 → 忽略，不影響上傳 */
  }
}

let _seq = 0;
const nextId = () => `u${Date.now()}_${_seq++}`;

export function summarize(items: UploadItem[]): UploadSummary {
  const s: UploadSummary = {
    total: items.length, done: 0, duplicate: 0, error: 0, pendingReview: 0, active: 0,
  };
  for (const it of items) {
    if (it.status === "done") s.done += 1;
    else if (it.status === "duplicate") s.duplicate += 1;
    else if (it.status === "error") s.error += 1;
    if (!isTerminal(it.status)) s.active += 1;
    if (it.pendingReview) s.pendingReview += 1;
  }
  return s;
}

/** 上傳佇列 hook：add(files) 累加到尾端並循序處理；items 含歷史（跨重整）。 */
export function useUploadQueue(uploadOne: (file: File) => Promise<UploadOutcome>) {
  const [items, setItems] = useState<UploadItem[]>(loadPersisted);
  const [running, setRunning] = useState(false);
  const pending = useRef<Array<{ id: string; file: File }>>([]);
  const runningRef = useRef(false);
  const uploadRef = useRef(uploadOne);
  uploadRef.current = uploadOne;

  // 未跑完的佇列因無 File 位元組無法續跑；重整前先提醒，避免整批白排。
  const activeCount = items.reduce((n, it) => n + (isTerminal(it.status) ? 0 : 1), 0);
  useEffect(() => {
    if (activeCount === 0) return;
    const warn = (e: BeforeUnloadEvent) => {
      e.preventDefault();
      e.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [activeCount]);

  const patch = useCallback((id: string, p: Partial<UploadItem>) => {
    setItems((prev) => {
      const next = prev.map((it) => (it.id === id ? { ...it, ...p } : it));
      persist(next);
      return next;
    });
  }, []);

  const drain = useCallback(async () => {
    if (runningRef.current) return;
    runningRef.current = true;
    setRunning(true);
    try {
      let job = pending.current.shift();
      while (job) {
        patch(job.id, { status: "uploading" });
        let outcome: UploadOutcome;
        try {
          outcome = await uploadRef.current(job.file);
        } catch (e) {
          outcome = { kind: "error", message: e instanceof Error ? e.message : "上傳失敗" };
        }
        if (outcome.kind === "done") {
          patch(job.id, {
            status: "done",
            message: outcome.ocr || "(無文字)",
            pendingReview: outcome.pendingReview,
          });
        } else if (outcome.kind === "duplicate") {
          patch(job.id, { status: "duplicate", message: outcome.message });
        } else {
          patch(job.id, { status: "error", message: outcome.message });
        }
        job = pending.current.shift();
      }
    } finally {
      runningRef.current = false;
      setRunning(false);
    }
  }, [patch]);

  const add = useCallback(
    (files: File[]) => {
      if (files.length === 0) return;
      const fresh: UploadItem[] = files.map((f) => ({
        id: nextId(), name: f.name, status: "queued" as const, message: "", pendingReview: false,
      }));
      setItems((prev) => [...prev, ...fresh]);
      fresh.forEach((it, i) => pending.current.push({ id: it.id, file: files[i] }));
      void drain();
    },
    [drain],
  );

  const clear = useCallback(() => {
    setItems([]);
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch {
      /* 忽略 */
    }
  }, []);

  return { items, running, add, clear };
}
