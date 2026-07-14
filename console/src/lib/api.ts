import type {
  AnnotationPatch,
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

/** 統一 fetch：補 API base + admin 認證標頭。所有 API 呼叫都走這個。 */
export function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  return fetch(API_BASE + path, {
    ...init,
    headers: { ...authHeaders(), ...(init.headers ?? {}) },
  });
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

export async function recommend(
  turns: Turn[],
  filters: Filters,
  params: Params,
): Promise<RecommendResponse> {
  const response = await apiFetch("/recommend", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(buildRecommendRequest(turns, filters, params)),
  });
  return unwrap<RecommendResponse>(response);
}

/** 梗圖大戰：上傳對方的梗圖，後端理解後推薦反擊梗（input_type=meme_battle）。 */
export async function recommendByMemeBattle(
  imageBase64: string,
  filters: Filters = DEFAULT_FILTERS,
  params: Params = DEFAULT_PARAMS,
): Promise<RecommendResponse> {
  const response = await apiFetch("/recommend", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      input_type: "meme_battle",
      image: imageBase64,
      conversation: [],
      filters,
      params,
      client_id: getClientId(),
    }),
  });
  return unwrap<RecommendResponse>(response);
}

/** 截圖直推（手機 client 主流程）：input_type=screenshot，後端一次完成解析＋推薦。 */
export async function recommendByScreenshot(
  imageBase64: string,
  filters: Filters = DEFAULT_FILTERS,
  params: Params = DEFAULT_PARAMS,
): Promise<RecommendResponse> {
  const response = await apiFetch("/recommend", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      input_type: "screenshot",
      image: imageBase64,
      conversation: [],
      filters,
      params,
      client_id: getClientId(),
    }),
  });
  return unwrap<RecommendResponse>(response);
}

// ── 非同步任務（免費端點延遲高：送出即回、背景執行、輪詢查結果）────────────

export type TaskInput =
  | { kind: "text"; text: string }
  | { kind: "screenshot"; image: string }
  | { kind: "battle"; image: string };

const INPUT_TYPE = { text: "text", screenshot: "screenshot", battle: "meme_battle" } as const;

/** 把手機端三種輸入統一組成 /tasks 的請求體（對齊 /recommend 契約）。 */
export function buildTaskRequest(input: TaskInput, filters: Filters, params: Params) {
  return {
    input_type: INPUT_TYPE[input.kind],
    conversation: input.kind === "text" ? [{ speaker: "other", text: input.text }] : [],
    image: input.kind === "text" ? null : input.image,
    filters,
    params,
    client_id: getClientId(),
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
): Promise<{ task_id: string; status: TaskStatus }> {
  const response = await apiFetch("/tasks", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(buildTaskRequest(input, filters, params)),
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

/** 分類版上傳（批次佇列用）：以 HTTP 狀態碼區分成功 / 重複 / 失敗，不丟例外。 */
export async function uploadMemeClassified(
  imageBase64: string,
  titleHint: string,
  model?: string,
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
      }),
    });
  } catch (e) {
    return { kind: "error", message: e instanceof Error ? e.message : "網路錯誤" };
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
