import type { Filters, Meta, Params, RecommendResponse, Turn } from "../types";

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
