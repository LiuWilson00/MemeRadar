/** 批次上傳佇列（seed 匯入口）：逐張跑「入庫 → 標註 → 向量化」並即時回報進度。
 *
 * 循序處理（非並發）：標註走 LLM、向量化走本地 BGE-M3，每張約 8–12 秒，
 * 逐張跑可即時看到每張完成、且不同時壓垮標註 API 與 embedder。
 * 單張失敗（重複 / 壞檔 / 網路）只標記該張，不中斷整批。
 */

export type UploadItemStatus = "queued" | "uploading" | "done" | "duplicate" | "error";

export type UploadOutcome =
  | { kind: "done"; memeId: string; pendingReview: boolean; ocr: string }
  | { kind: "duplicate"; message: string }
  | { kind: "error"; message: string };

export interface UploadItem {
  name: string;
  status: UploadItemStatus;
  message: string;
  pendingReview: boolean;
}

export interface UploadSummary {
  done: number;
  duplicate: number;
  error: number;
  pendingReview: number;
}

export async function runUploadQueue(
  files: File[],
  uploadOne: (file: File) => Promise<UploadOutcome>,
  onProgress: (items: UploadItem[]) => void,
): Promise<UploadSummary> {
  const items: UploadItem[] = files.map((f) => ({
    name: f.name,
    status: "queued",
    message: "",
    pendingReview: false,
  }));
  const summary: UploadSummary = { done: 0, duplicate: 0, error: 0, pendingReview: 0 };
  const emit = () => onProgress(items.map((item) => ({ ...item })));
  emit();

  for (let i = 0; i < files.length; i++) {
    items[i].status = "uploading";
    emit();

    let outcome: UploadOutcome;
    try {
      outcome = await uploadOne(files[i]);
    } catch (e) {
      outcome = { kind: "error", message: e instanceof Error ? e.message : "上傳失敗" };
    }

    if (outcome.kind === "done") {
      items[i].status = "done";
      items[i].message = outcome.ocr || "(無文字)";
      items[i].pendingReview = outcome.pendingReview;
      summary.done += 1;
      if (outcome.pendingReview) summary.pendingReview += 1;
    } else if (outcome.kind === "duplicate") {
      items[i].status = "duplicate";
      items[i].message = outcome.message;
      summary.duplicate += 1;
    } else {
      items[i].status = "error";
      items[i].message = outcome.message;
      summary.error += 1;
    }
    emit();
  }

  return summary;
}
