import type {
  AnnotationPatch,
  DedupReviewItem,
  FeedbackReport,
  Filters,
  HistoryDetail,
  HistoryItem,
  LibraryMeme,
  Meta,
  Params,
  RecommendResponse,
  ScreenshotParse,
  Turn,
  UploadResult,
} from "../types";

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
  const response = await fetch("/recommend", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(buildRecommendRequest(turns, filters, params)),
  });
  return unwrap<RecommendResponse>(response);
}

export async function sendFeedback(body: {
  query_id: string;
  meme_id: string;
  rank: number;
  rating: "up" | "down";
  note?: string | null;
}): Promise<void> {
  const response = await fetch("/feedback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  await unwrap(response);
}

export async function fetchMeta(): Promise<Meta> {
  return unwrap<Meta>(await fetch("/meta"));
}

export async function fetchHistory(): Promise<HistoryItem[]> {
  return unwrap<HistoryItem[]>(await fetch("/history"));
}

export async function fetchHistoryDetail(queryId: string): Promise<HistoryDetail> {
  return unwrap<HistoryDetail>(await fetch(`/history/${queryId}`));
}

export async function fetchMemes(filters: {
  franchise?: string;
  category?: string;
  emotion?: string;
  status?: string;
}): Promise<LibraryMeme[]> {
  const query = new URLSearchParams(
    Object.entries(filters).filter(([, v]) => v) as [string, string][],
  );
  return unwrap<LibraryMeme[]>(await fetch(`/memes?${query}`));
}

/** 手動上傳（seed 匯入口）：匯入 → 標註 → 向量化，約 8–12 秒。 */
export async function uploadMeme(imageBase64: string, titleHint: string): Promise<UploadResult> {
  const response = await fetch("/memes", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ image: imageBase64, title_hint: titleHint || null }),
  });
  return unwrap<UploadResult>(response);
}

export async function fetchFeedbackReport(): Promise<FeedbackReport> {
  return unwrap<FeedbackReport>(await fetch("/report/feedback"));
}

/** 標註複核：通過（可帶標籤修補，後端會重建向量）或淘汰。 */
export async function reviewAnnotation(
  memeId: string,
  action: "approve" | "remove",
  patch?: AnnotationPatch,
): Promise<void> {
  const response = await fetch(`/review/annotations/${memeId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, patch: patch ?? null }),
  });
  await unwrap(response);
}

export async function fetchDedupReviews(): Promise<DedupReviewItem[]> {
  return unwrap<DedupReviewItem[]>(await fetch("/review/dedup"));
}

export async function resolveDedup(
  reviewId: string,
  resolution: "merged" | "distinct",
): Promise<void> {
  const response = await fetch(`/review/dedup/${reviewId}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ resolution }),
  });
  await unwrap(response);
}

/** 截圖 → 結構化對話（後端僅記憶體處理，不落庫）。約 5–8 秒。 */
export async function parseScreenshot(imageBase64: string): Promise<ScreenshotParse> {
  const response = await fetch("/parse-screenshot", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ image: imageBase64 }),
  });
  return unwrap<ScreenshotParse>(response);
}
