/**
 * 操作麵包屑：記憶體 ring buffer，記錄使用者最近的操作與關鍵事件，供 bug 回報附帶。
 *
 * 隱私輕量：只記「動作類型 + 少量 metadata」（如搜尋類型、結果張數、API 狀態碼），
 * *不*記完整對話文字或圖片。時間用相對毫秒（自載入起算）。
 */

export interface Breadcrumb {
  t: number; // 自頁面載入起算的毫秒
  type: string; // nav | action | result | api | error | quota …
  msg: string;
  data?: Record<string, unknown>;
}

const MAX = 80;
const buffer: Breadcrumb[] = [];
const start = Date.now();

export function logBreadcrumb(
  type: string,
  msg: string,
  data?: Record<string, unknown>,
): void {
  buffer.push({ t: Date.now() - start, type, msg, ...(data ? { data } : {}) });
  if (buffer.length > MAX) buffer.splice(0, buffer.length - MAX);
}

/** 回傳目前麵包屑的副本（新到舊呈現由呈現端決定；此處維持記錄順序）。 */
export function getBreadcrumbs(): Breadcrumb[] {
  return buffer.slice();
}

/** 測試/清空用。 */
export function clearBreadcrumbs(): void {
  buffer.length = 0;
}
