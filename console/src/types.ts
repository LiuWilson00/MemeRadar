/** API 型別（契約：docs/01 §5.2） */

export interface Turn {
  speaker: "me" | "other";
  text: string;
}

export interface Filters {
  franchises: string[];
  categories: string[];
  exclude_nsfw: boolean;
}

export interface Params {
  top_n: number;
  candidate_k: number;
  min_similarity: number;
  diversity: number;
  hotness_weight: number;
}

export interface StrategyPlan {
  name: string;
  rationale: string;
  query: string;
}

export interface Intent {
  summary: string;
  punchline: string;
  other_party_emotion: string[];
  conversation_type: string;
  sensitive: boolean;
  low_context: boolean;
  language: string;
  strategies: StrategyPlan[];
}

export interface ResultItem {
  meme_id: string;
  image_url: string;
  rank: number;
  scores: { vector: number; rerank: number; final: number };
  matched_strategy: string;
  matched_tags: string[];
  reason: string;
}

export interface CandidateDebug {
  meme_id: string;
  ocr_text: string;
  vector: number;
  per_strategy: Record<string, number>;
  in_top: boolean;
}

export interface RecommendResponse {
  query_id: string;
  intent: Intent;
  results: ResultItem[];
  debug: {
    queries: string[];
    candidates: CandidateDebug[];
    per_strategy_hits: Record<string, number>;
    rerank_fallback: boolean;
    timings_ms: Record<string, number>;
  };
}

export interface ScreenshotParse {
  app_guess: string;
  conversation: Array<{ speaker: "me" | "other"; text: string; confidence: number }>;
  warnings: string[];
}

export interface Meta {
  franchises: Array<{ name: string; count: number }>;
  categories: string[];
  strategies: string[];
}
