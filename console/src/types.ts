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

export interface ModelTaskSetting {
  key: string;
  label: string;
  current: string | null; // null = 用 VLM 預設
}

export interface ModelSettings {
  tasks: ModelTaskSetting[];
  available: string[];
  default: string | null;
}

export interface VlmUsageRow {
  key_id: string | null;
  status: string;
  n: number;
  avg_ms: number | null;
}

export type TaskStatus = "pending" | "running" | "done" | "error";

/** 歷史列表項（精簡，不含完整 result；has_result 為 SQLite 0/1）。 */
export interface TaskSummary {
  task_id: string;
  client_id: string;
  input_type: "text" | "screenshot" | "meme_battle";
  label: string;
  status: TaskStatus;
  error: string | null;
  created_at: string;
  updated_at: string;
  has_result: number;
}

/** 單一任務進度 / 結果（GET /tasks/{id}）。 */
export interface TaskDetail {
  task_id: string;
  client_id: string;
  input_type: "text" | "screenshot" | "meme_battle";
  label: string;
  status: TaskStatus;
  error: string | null;
  created_at: string;
  updated_at: string;
  result: RecommendResponse | null;
}

export interface HistoryItem {
  query_id: string;
  created_at: string;
  conversation: Turn[];
  params_snapshot: { filters?: Filters; params?: Params };
  latency_ms: number | null;
  result_count: number;
  ups: number;
  downs: number;
}

export interface HistoryDetail {
  query_id: string;
  conversation: Turn[];
  params_snapshot: { filters?: Filters; params?: Params };
  intent_result: Intent | null;
  final_results: ResultItem[] | null;
  latency_ms: number | null;
  created_at: string;
}

export interface LibraryMeme {
  meme_id: string;
  image_url: string;
  status: string;
  hotness: number;
  first_seen_at: string;
  annotation: {
    is_meme: boolean;
    nsfw: boolean;
    ocr_text: string;
    description: string;
    characters: string[];
    franchise: string | null;
    template_name: string | null;
    emotions: string[];
    usage_hints: string[];
    categories: string[];
    confidence: number | null;
    model_version: string;
  } | null;
}

export interface UploadResult {
  meme_id: string;
  status: string;
  meme_status: string;
  annotation: LibraryMeme["annotation"];
  embedded: boolean;
  image_url: string;
}

export interface Meta {
  franchises: Array<{ name: string; count: number }>;
  categories: string[];
  strategies: string[];
  emotions: string[];
}

export interface GroupRow {
  key: string | number;
  ups: number;
  downs: number;
  up_rate: number | null;
}

export interface FeedbackReport {
  totals: { ups: number; downs: number; total: number; up_rate: number | null };
  queries_with_feedback: number;
  daily: Array<{ date: string; ups: number; downs: number }>;
  by_strategy: GroupRow[];
  by_franchise: GroupRow[];
  by_rank: GroupRow[];
  by_params: GroupRow[];
  down_notes: Array<{
    created_at: string;
    query_id: string;
    note: string;
    meme_id: string;
    meme_ocr: string;
    rank: number;
    matched_strategy: string;
    intent_summary: string;
  }>;
}

export interface DedupReviewItem {
  review_id: string;
  layer: string;
  score: number | null;
  created_at: string;
  meme: { meme_id: string; image_url: string; ocr_text: string; status: string };
  matched: { meme_id: string; image_url: string; ocr_text: string; status: string };
}

export interface AnnotationPatch {
  ocr_text?: string;
  franchise?: string | null;
  emotions?: string[];
  usage_hints?: string[];
  categories?: string[];
  nsfw?: boolean;
  is_meme?: boolean;
}
