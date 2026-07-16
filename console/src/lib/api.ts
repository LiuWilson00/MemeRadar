import type {
  AnnotationPatch,
  BugReport,
  ChatFeedbackRow,
  ChatReply,
  ClientError,
  DedupReviewItem,
  FeedbackReport,
  Filters,
  GalleryItem,
  HistoryDetail,
  HistoryItem,
  LeaderboardEntry,
  LibraryMeme,
  MemeComment,
  Meta,
  Dashboard,
  ModelSettings,
  Params,
  RecommendResponse,
  ReportedMeme,
  ScreenshotParse,
  TaskDetail,
  TaskStatus,
  TaskSummary,
  Turn,
  UploadResult,
  User,
  VlmUsageRow,
} from "../types";
import { getUserToken } from "./auth";
import { getBreadcrumbs, logBreadcrumb } from "./breadcrumbs";
import { getClientId } from "./clientId";
import type { UploadOutcome } from "./uploadQueue";

// API base：跨源部署時設 VITE_API_BASE_URL（build 期注入）；本地 / 同源部署留空＝相對路徑。
const API_BASE = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "";

/** 認證標頭：前台使用者已登入 → Bearer（優先）；否則帶後台 admin 的 Basic（若有）。
 * 兩者不會同時需要（前台無 admin 帳密、後台無 Google 登入），故單一 Authorization 即可。 */
function authHeaders(): Record<string, string> {
  const token = getUserToken();
  if (token) return { Authorization: `Bearer ${token}` };
  if (typeof sessionStorage === "undefined") return {};
  const creds = sessionStorage.getItem("memeradar.adminAuth");
  return creds ? { Authorization: `Basic ${creds}` } : {};
}

/** 統一 fetch：補 API base + admin 認證標頭 + 失敗自動留麵包屑。所有 API 呼叫都走這個。 */
export async function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const method = (init.method ?? "GET").toUpperCase();
  try {
    const res = await fetch(API_BASE + path, {
      ...init,
      headers: { ...authHeaders(), ...(init.headers ?? {}) },
    });
    if (!res.ok && path !== "/bug-reports") {
      logBreadcrumb("api", `${method} ${path} → ${res.status}`, { status: res.status });
    }
    return res;
  } catch (e) {
    logBreadcrumb("api", `${method} ${path} → 網路錯誤`, { error: String(e) });
    throw e;
  }
}

/** 圖片等資源的完整 URL（跨源部署時要帶 API base）。只對 / 開頭的相對路徑加，
 * data:／完整 URL 原樣通過，故可重複套用而不會重複加前綴。 */
export function imageUrl(path: string): string {
  return path.startsWith("/") ? API_BASE + path : path;
}

export const DEFAULT_FILTERS: Filters = {
  franchises: [],
  categories: [],
  exclude_nsfw: true,
};

/** 預設值對齊 docs/04 §3 */
export const DEFAULT_PARAMS: Params = {
  top_n: 5,
  candidate_k: 50,
  min_similarity: 0.35,
  diversity: 0.5,
  hotness_weight: 0.1,
};

export function buildRecommendRequest(turns: Turn[], filters: Filters, params: Params) {
  return {
    input_type: "text" as const,
    conversation: turns,
    filters,
    params,
    client_id: getClientId(),
    debug: true, // 後台工作台的 DebugPanel 需要候選池明細（一般 client 不送、省流量）
  };
}

async function unwrap<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const detail = await response
      .json()
      .then((body) => body.detail ?? response.statusText)
      .catch(() => response.statusText);
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return response.json() as Promise<T>;
}

/** 送出多輪對話到背景佇列（/tasks），回 task_id。 */
async function submitConversationTask(
  turns: Turn[],
  filters: Filters,
  params: Params,
): Promise<{ task_id: string; status: TaskStatus }> {
  const response = await apiFetch("/tasks", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(buildRecommendRequest(turns, filters, params)),
  });
  return unwrap(response);
}

/** 後台工作台推薦：與 recommend() 同介面，但走背景佇列（送出→輪詢），
 * 不占用同步請求連線 —— 慢搜尋不會拖住 API 的請求連線池。 */
export async function recommendViaTask(
  turns: Turn[],
  filters: Filters,
  params: Params,
): Promise<RecommendResponse> {
  const { task_id } = await submitConversationTask(turns, filters, params);
  const deadline = Date.now() + 120_000; // 最多輪詢 2 分鐘
  for (;;) {
    await new Promise((r) => setTimeout(r, 1500));
    const t = await fetchTask(task_id);
    if (t.status === "done" && t.result) return t.result;
    if (t.status === "error") throw new Error(t.error ?? "推薦失敗");
    if (t.status === "cancelled") throw new Error("已取消");
    if (Date.now() > deadline) throw new Error("推薦逾時，請稍後再試");
  }
}

// ── 非同步任務（免費端點延遲高：送出即回、背景執行、輪詢查結果）────────────

export type TaskInput =
  | { kind: "text"; text: string }
  | { kind: "screenshot"; image: string }
  | { kind: "battle"; image: string };

const INPUT_TYPE = { text: "text", screenshot: "screenshot", battle: "meme_battle" } as const;

/** 把手機端三種輸入統一組成 /tasks 的請求體（對齊 /recommend 契約）。 */
export function buildTaskRequest(
  input: TaskInput,
  filters: Filters,
  params: Params,
  fastMode = false,
  variety = false,
) {
  return {
    input_type: INPUT_TYPE[input.kind],
    conversation: input.kind === "text" ? [{ speaker: "other", text: input.text }] : [],
    image: input.kind === "text" ? null : input.image,
    filters,
    params,
    client_id: getClientId(),
    fast_mode: fastMode,
    variety,
  };
}

/** 未登入者當日推薦配額用罄（後端回 429 + quota_exceeded）。前台用它切換到登入引導。 */
export class QuotaError extends Error {
  limit: number;
  constructor(message: string, limit: number) {
    super(message);
    this.name = "QuotaError";
    this.limit = limit;
  }
}

/** 送出非同步推薦任務，回 task_id（實際運算在後端背景進行）。 */
export async function submitTask(
  input: TaskInput,
  filters: Filters,
  params: Params,
  fastMode = false,
  variety = false,
): Promise<{ task_id: string; status: TaskStatus }> {
  const response = await apiFetch("/tasks", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(buildTaskRequest(input, filters, params, fastMode, variety)),
  });
  if (response.status === 429) {
    const detail = await response.json().then((b) => b?.detail).catch(() => null);
    if (detail && typeof detail === "object" && detail.error === "quota_exceeded") {
      throw new QuotaError(detail.message ?? "今天的免費次數已用完", detail.limit ?? 5);
    }
    throw new Error(typeof detail === "string" ? detail : "請求過於頻繁，請稍後再試");
  }
  return unwrap(response);
}

/** 查單一任務進度 / 結果（前台輪詢）。 */
export async function fetchTask(taskId: string): Promise<TaskDetail> {
  return unwrap<TaskDetail>(await apiFetch(`/tasks/${encodeURIComponent(taskId)}`));
}

/** 取消進行中的搜尋任務（best-effort，不丟例外給 UI）。 */
export async function cancelTask(taskId: string): Promise<void> {
  try {
    await apiFetch(
      `/tasks/${encodeURIComponent(taskId)}/cancel?client_id=${encodeURIComponent(getClientId())}`,
      { method: "POST" },
    );
  } catch {
    /* 取消是 best-effort：前端已停止輪詢，後端標記失敗也無妨 */
  }
}

/** 本機 client 的歷史任務（新到舊）。 */
export async function fetchTaskHistory(): Promise<TaskSummary[]> {
  return unwrap<TaskSummary[]>(await apiFetch(`/tasks?client_id=${encodeURIComponent(getClientId())}`));
}

export async function sendFeedback(body: {
  query_id: string;
  meme_id: string;
  rank: number;
  rating: "up" | "down";
  note?: string | null;
}): Promise<void> {
  const response = await apiFetch("/feedback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  await unwrap(response);
}

export async function fetchMeta(): Promise<Meta> {
  return unwrap<Meta>(await apiFetch("/meta"));
}

/** 只會回梗圖的朋友：一則訊息 → 一張梗圖（exclude 帶這輪回過的，避免重複）。 */
export async function chat(message: string, exclude: string[] = []): Promise<ChatReply> {
  const response = await apiFetch("/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, client_id: getClientId(), exclude }),
  });
  return unwrap<ChatReply>(response);
}

/** 評價梗友的一則回覆（👍/👎）；best-effort，帶觸發訊息供優化。 */
export function sendChatFeedback(memeId: string, message: string, rating: "up" | "down"): void {
  void apiFetch("/chat/feedback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ meme_id: memeId, message, rating, client_id: getClientId() }),
  }).catch(() => {});
}

/** 後台：梗友回覆評價清單。 */
export async function fetchChatFeedback(limit = 200): Promise<ChatFeedbackRow[]> {
  return unwrap<ChatFeedbackRow[]>(await apiFetch(`/chat/feedback?limit=${limit}`));
}

// ── 前台錯誤回報（best-effort，供後台 debug）────────────────────────────
// 同一訊息每 session 只報一次、上限 20 筆，避免壞頁狂灌後端。
const reportedErrors = new Set<string>();
let reportCount = 0;

export function reportClientError(message: string, opts: { stack?: string; url?: string } = {}): void {
  const msg = (message || "").trim();
  if (!msg) return;
  const key = msg.slice(0, 200);
  if (reportedErrors.has(key) || reportCount >= 20) return;
  reportedErrors.add(key);
  reportCount += 1;
  const url =
    opts.url ?? (typeof location !== "undefined" ? location.pathname + location.search : undefined);
  void apiFetch("/client-errors", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message: msg.slice(0, 1000),
      stack: opts.stack?.slice(0, 4000) ?? null,
      url: url ?? null,
      client_id: getClientId(),
    }),
  }).catch(() => {});
}

/** 後台：最近的前台錯誤。 */
export async function fetchClientErrors(limit = 100): Promise<ClientError[]> {
  return unwrap<ClientError[]>(await apiFetch(`/client-errors?limit=${limit}`));
}

/** 送出使用者問題回報：描述 + 操作麵包屑 + 裝置資訊。丟例外供 UI 顯示成敗。 */
export async function sendBugReport(description: string): Promise<void> {
  const url =
    typeof location !== "undefined" ? location.pathname + location.hash : undefined;
  const meta =
    typeof window !== "undefined"
      ? { vw: window.innerWidth, vh: window.innerHeight, lang: navigator.language }
      : {};
  const res = await apiFetch("/bug-reports", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      description: description.slice(0, 2000),
      breadcrumbs: getBreadcrumbs(),
      url,
      user_agent: typeof navigator !== "undefined" ? navigator.userAgent : undefined,
      meta,
      client_id: getClientId(),
    }),
  });
  if (!res.ok) throw new Error("回報失敗，請稍後再試");
}

export async function fetchBugReports(limit = 200): Promise<BugReport[]> {
  return unwrap<BugReport[]>(await apiFetch(`/bug-reports?limit=${limit}`));
}

/** Google 登入：把 Google 回傳的 credential 換成我方 session token + 使用者資料。 */
export async function googleLogin(credential: string): Promise<{ token: string; user: User }> {
  const response = await apiFetch("/auth/google", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ credential }),
  });
  return unwrap<{ token: string; user: User }>(response);
}

/** 取目前登入使用者（需帶有效 Bearer）；未登入回 401。 */
export async function fetchMe(): Promise<User> {
  return unwrap<User>(await apiFetch("/auth/me"));
}

/** 登入使用者設定顯示暱稱。 */
export async function setNickname(nickname: string): Promise<void> {
  const response = await apiFetch("/auth/nickname", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ nickname }),
  });
  await unwrap(response);
}

// ── 探索圖庫 ─────────────────────────────────────────────────────────

/** 探索圖庫一頁（seed 讓隨機排序在分頁間穩定）。 */
export async function fetchGallery(
  seed: string,
  offset: number,
  limit = 24,
): Promise<GalleryItem[]> {
  const query = new URLSearchParams({
    client_id: getClientId(),
    seed,
    offset: String(offset),
    limit: String(limit),
  });
  return unwrap<GalleryItem[]>(await apiFetch(`/gallery?${query}`));
}

/** 按讚 / 取消讚（回新的讚數與狀態）。 */
export async function toggleLike(memeId: string): Promise<{ likes: number; liked: boolean }> {
  const response = await apiFetch(`/memes/${memeId}/like`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ client_id: getClientId() }),
  });
  return unwrap(response);
}

/** 單張梗圖詳情（分享冷載入 / deep-link 用）。 */
export async function fetchMeme(memeId: string): Promise<GalleryItem> {
  return unwrap<GalleryItem>(
    await apiFetch(`/memes/${memeId}?client_id=${encodeURIComponent(getClientId())}`),
  );
}

/** 收藏 / 取消收藏（需登入；Bearer 由 authHeaders 自動帶）。 */
export async function toggleFavorite(memeId: string, on: boolean): Promise<void> {
  const res = await apiFetch(`/memes/${memeId}/favorite`, { method: on ? "POST" : "DELETE" });
  if (!res.ok) throw new Error("收藏失敗，請確認已登入");
}

/** 登入使用者的收藏清單（新到舊）。 */
export async function fetchFavorites(): Promise<GalleryItem[]> {
  return unwrap<GalleryItem[]>(await apiFetch("/favorites"));
}

/** 分享網址：分享頁在 API 端（帶 OG 預覽），點進去自動導向 app 的 /m/{id} detail。 */
export function shareUrl(memeId: string): string {
  const base = API_BASE || (typeof location !== "undefined" ? location.origin : "");
  return `${base}/m/${memeId}`;
}

/** 分享或複製：手機優先叫原生分享面板（可直接送 Line），否則複製到剪貼簿。 */
export async function shareOrCopy(memeId: string): Promise<"shared" | "copied"> {
  const url = shareUrl(memeId);
  if (typeof navigator !== "undefined" && navigator.share) {
    try {
      await navigator.share({ title: "MemeRadar 梗圖", url });
      return "shared";
    } catch (e) {
      if ((e as { name?: string })?.name === "AbortError") return "shared"; // 使用者取消
    }
  }
  await navigator.clipboard.writeText(url);
  return "copied";
}

export async function fetchComments(memeId: string): Promise<MemeComment[]> {
  const query = new URLSearchParams({ client_id: getClientId() });
  return unwrap<MemeComment[]>(await apiFetch(`/memes/${memeId}/comments?${query}`));
}

export async function addComment(
  memeId: string,
  authorName: string,
  text: string,
): Promise<MemeComment> {
  const response = await apiFetch(`/memes/${memeId}/comments`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ client_id: getClientId(), author_name: authorName, text }),
  });
  return unwrap<MemeComment>(response);
}

export async function editComment(memeId: string, commentId: string, text: string): Promise<void> {
  const response = await apiFetch(`/memes/${memeId}/comments/${commentId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ client_id: getClientId(), text }),
  });
  await unwrap(response);
}

export async function deleteComment(memeId: string, commentId: string): Promise<void> {
  const query = new URLSearchParams({ client_id: getClientId() });
  const response = await apiFetch(`/memes/${memeId}/comments/${commentId}?${query}`, {
    method: "DELETE",
  });
  await unwrap(response);
}

/** 前台檢舉一張梗圖（不宜 / 冒犯）。best-effort：失敗也不打擾使用者。 */
export async function reportMeme(memeId: string, reason?: string): Promise<void> {
  await apiFetch(`/memes/${memeId}/report`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reason: reason ?? null, client_id: getClientId() }),
  }).catch(() => {});
}

/** 後台：被檢舉且未處理的梗圖清單。 */
export async function fetchReports(): Promise<ReportedMeme[]> {
  return unwrap<ReportedMeme[]>(await apiFetch("/review/reports"));
}

/** 後台：處理被檢舉的梗圖——remove 下架、dismiss 保留；兩者都清出清單。 */
export async function resolveReport(memeId: string, action: "remove" | "dismiss"): Promise<void> {
  const response = await apiFetch(`/review/reports/${memeId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action }),
  });
  await unwrap(response);
}

/** 熱門梗圖榜（讚×3 + 下載）。資料少時後端自然回短 / 空清單。 */
export async function fetchLeaderboard(limit = 20): Promise<LeaderboardEntry[]> {
  return unwrap<LeaderboardEntry[]>(await apiFetch(`/leaderboard?limit=${limit}`));
}

/** 記一筆前台行為事件（下載 / 選分類）。best-effort：失敗也不擋使用者流程。 */
export function logEvent(
  eventType: "download" | "category" | "search",
  opts: { memeId?: string; meta?: Record<string, unknown> } = {},
): void {
  void apiFetch("/events", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      event_type: eventType,
      client_id: getClientId(),
      meme_id: opts.memeId ?? null,
      meta: opts.meta ?? null,
    }),
  }).catch(() => {});
}

export async function fetchHistory(): Promise<HistoryItem[]> {
  return unwrap<HistoryItem[]>(await apiFetch("/history"));
}

export async function fetchHistoryDetail(queryId: string): Promise<HistoryDetail> {
  return unwrap<HistoryDetail>(await apiFetch(`/history/${queryId}`));
}

export async function fetchMemes(
  filters: {
    franchise?: string;
    category?: string;
    emotion?: string;
    status?: string;
  },
  limit = 200,
): Promise<LibraryMeme[]> {
  const query = new URLSearchParams(
    Object.entries(filters).filter(([, v]) => v) as [string, string][],
  );
  query.set("limit", String(limit));
  return unwrap<LibraryMeme[]>(await apiFetch(`/memes?${query}`));
}

/** 手動上傳（seed 匯入口）：匯入 → 標註 → 向量化，約 8–12 秒。 */
export async function uploadMeme(imageBase64: string, titleHint: string): Promise<UploadResult> {
  const response = await apiFetch("/memes", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ image: imageBase64, title_hint: titleHint || null }),
  });
  return unwrap<UploadResult>(response);
}

async function errorDetail(response: Response, fallback: string): Promise<string> {
  return response
    .json()
    .then((body) => (typeof body.detail === "string" ? body.detail : fallback))
    .catch(() => fallback);
}

/** 使用者上傳到共用圖庫的結果（以 HTTP 狀態碼分流，不丟例外）。 */
export type LibraryUploadOutcome =
  | { kind: "published"; memeId: string; ocr: string; franchise: string | null }
  | { kind: "duplicate"; message: string }
  | { kind: "rejected"; message: string } // NSFW / 非梗圖 / 壞圖
  | { kind: "quota"; message: string }
  | { kind: "error"; message: string };

/** 登入使用者上傳梗圖到共用圖庫（登入才可用）。約 8–12 秒（含標註）。 */
export async function uploadToLibrary(
  imageBase64: string,
  titleHint?: string,
): Promise<LibraryUploadOutcome> {
  let response: Response;
  try {
    response = await apiFetch("/library/memes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ image: imageBase64, title_hint: titleHint || null }),
    });
  } catch (e) {
    return { kind: "error", message: e instanceof Error ? e.message : "網路錯誤" };
  }
  if (response.status === 201) {
    const body = (await response.json()) as {
      meme_id: string;
      annotation?: { ocr_text?: string; franchise?: string | null };
    };
    return {
      kind: "published",
      memeId: body.meme_id,
      ocr: body.annotation?.ocr_text ?? "",
      franchise: body.annotation?.franchise ?? null,
    };
  }
  if (response.status === 401) return { kind: "error", message: "請先登入" };
  if (response.status === 409) {
    return { kind: "duplicate", message: await errorDetail(response, "圖庫已經有這張了") };
  }
  if (response.status === 429) {
    const detail = await response.json().then((b) => b?.detail).catch(() => null);
    const message =
      detail && typeof detail === "object"
        ? detail.message
        : typeof detail === "string"
          ? detail
          : "太頻繁了，稍後再試";
    return { kind: "quota", message };
  }
  return { kind: "rejected", message: await errorDetail(response, "這張無法上架") };
}

export async function fetchVlmModels(): Promise<{ models: string[]; default: string | null }> {
  return unwrap(await apiFetch("/vlm/models"));
}

/** 後台：待背景標註的張數（大量匯入時顯示進度）。 */
export async function fetchAnnotationPending(): Promise<{ pending: number }> {
  return unwrap<{ pending: number }>(await apiFetch("/annotation/pending"));
}

/** 後台：各任務模型設定（含可選清單與 VLM 預設）。 */
export async function fetchModelSettings(): Promise<ModelSettings> {
  return unwrap<ModelSettings>(await apiFetch("/settings/models"));
}

/** 後台：設定各任務模型；值為 null / 空字串 = 回預設。 */
export async function updateModelSettings(
  models: Record<string, string | null>,
): Promise<void> {
  const response = await apiFetch("/settings/models", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ models }),
  });
  await unwrap(response);
}

/** 後台：NVIDIA 呼叫用量（各 key × 狀態的呼叫數與平均延遲）。 */
export async function fetchVlmUsage(): Promise<VlmUsageRow[]> {
  return unwrap<VlmUsageRow[]>(await apiFetch("/vlm/usage"));
}

/** 分類版上傳（批次佇列用）：以 HTTP 狀態碼區分成功 / 重複 / 失敗，不丟例外。
 * annotate=false → 只入庫（秒級），標註交給後端背景 worker（大量匯入不卡）。 */
export async function uploadMemeClassified(
  imageBase64: string,
  titleHint: string,
  model?: string,
  annotate = true,
): Promise<UploadOutcome> {
  let response: Response;
  try {
    response = await apiFetch("/memes", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        image: imageBase64,
        title_hint: titleHint || null,
        model: model || null,
        annotate,
      }),
      // 安全逾時：只入庫時秒級回；同步標註最壞約 180s。3 分鐘還沒回就當逾時、放行佇列
      signal: AbortSignal.timeout(180_000),
    });
  } catch (e) {
    const timedOut = e instanceof DOMException && e.name === "TimeoutError";
    return {
      kind: "error",
      message: timedOut ? "逾時（伺服器忙碌／限流），可稍後重傳" : e instanceof Error ? e.message : "網路錯誤",
    };
  }
  if (response.status === 409) {
    return { kind: "duplicate", message: await errorDetail(response, "圖片已存在") };
  }
  if (!response.ok) {
    return { kind: "error", message: await errorDetail(response, response.statusText) };
  }
  const result = (await response.json()) as UploadResult;
  return {
    kind: "done",
    memeId: result.meme_id,
    pendingReview: result.meme_status === "pending_review",
    ocr: result.annotation?.ocr_text ?? "",
  };
}

export async function fetchFeedbackReport(): Promise<FeedbackReport> {
  return unwrap<FeedbackReport>(await apiFetch("/report/feedback"));
}

/** 後台監控儀表板：使用量 / 延遲 / NVIDIA 用量 / 標註速度 / 回饋 / 圖庫。 */
export async function fetchDashboard(): Promise<Dashboard> {
  return unwrap<Dashboard>(await apiFetch("/report/dashboard"));
}

/** 標註複核：通過（可帶標籤修補，後端會重建向量）或淘汰。 */
export async function reviewAnnotation(
  memeId: string,
  action: "approve" | "remove",
  patch?: AnnotationPatch,
): Promise<void> {
  const response = await apiFetch(`/review/annotations/${memeId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, patch: patch ?? null }),
  });
  await unwrap(response);
}

export async function fetchDedupReviews(): Promise<DedupReviewItem[]> {
  return unwrap<DedupReviewItem[]>(await apiFetch("/review/dedup"));
}

export async function resolveDedup(
  reviewId: string,
  resolution: "merged" | "distinct",
): Promise<void> {
  const response = await apiFetch(`/review/dedup/${reviewId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ resolution }),
  });
  await unwrap(response);
}

/** 截圖 → 結構化對話（後端僅記憶體處理，不落庫）。約 5–8 秒。 */
export async function parseScreenshot(imageBase64: string): Promise<ScreenshotParse> {
  const response = await apiFetch("/parse-screenshot", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ image: imageBase64 }),
  });
  return unwrap<ScreenshotParse>(response);
}
