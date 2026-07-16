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
    fast?: { source: string; ocr_text?: string; labels?: string[] };
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

export interface Dashboard {
  overview: {
    recommendations_total: number;
    recommendations_7d: number;
    unique_clients: number;
    tasks_total: number;
    memes_active: number;
    memes_total: number;
    embeddings: number;
    annotations: number;
    vlm_calls_total: number;
    feedback_ups: number;
    feedback_downs: number;
    feedback_up_rate: number | null;
    embedding_coverage: number | null;
  };
  tasks_by_status: Record<string, number>;
  daily_recommendations: { date: string; count: number }[];
  latency_ms: Record<string, number | null>;
  vlm_calls: { task: string; status: string; count: number; avg_ms: number | null }[];
  library: {
    by_franchise: { name: string; count: number }[];
    by_category: { name: string; count: number }[];
  };
}

export type TaskStatus = "pending" | "running" | "done" | "error" | "cancelled";

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

/** 登入使用者（Google 登入後由後端回傳的公開欄位）。 */
export interface User {
  user_id: string;
  email: string | null;
  name: string | null;
  picture: string | null;
  role: string;
  nickname: string | null;
}

/** 探索圖庫一張卡（含尺寸供瀑布流、讚/留言數、此裝置是否已讚）。 */
export interface GalleryItem {
  meme_id: string;
  image_url: string;
  width: number | null;
  height: number | null;
  ocr_text: string | null;
  franchise: string | null;
  likes: number;
  comments: number;
  liked: boolean;
  favorited?: boolean; // 登入使用者是否已收藏（僅單張詳情 / 收藏列表帶）
}

/** 一則彈幕留言。 */
export interface MemeComment {
  comment_id: string;
  author_name: string;
  text: string;
  created_at: string;
  edited: boolean;
  mine: boolean;
}

/** 後台：一筆梗友回覆的評價（供優化選圖）。 */
export interface ChatFeedbackRow {
  event_id: string;
  client_id: string | null;
  meme_id: string;
  rating: "up" | "down" | null;
  message: string | null;
  ocr_text: string | null;
  franchise: string | null;
  created_at: string;
}

/** 一筆操作麵包屑（bug 回報附帶的最近操作紀錄）。 */
export interface Breadcrumb {
  t: number;
  type: string;
  msg: string;
  data?: Record<string, unknown>;
}

/** 後台：一筆使用者主動回報的問題。 */
export interface BugReport {
  report_id: string;
  description: string;
  breadcrumbs: Breadcrumb[];
  url: string | null;
  user_agent: string | null;
  client_id: string | null;
  meta: Record<string, unknown>;
  created_at: string;
}

/** 後台：一筆前台回報的瀏覽器錯誤。 */
export interface ClientError {
  error_id: string;
  message: string;
  stack: string | null;
  url: string | null;
  user_agent: string | null;
  client_id: string | null;
  created_at: string;
}

/** 後台被檢舉的梗圖一列。 */
export interface ReportedMeme {
  meme_id: string;
  ocr_text: string | null;
  franchise: string | null;
  status: string;
  reports: number;
  last_reported: string;
}

/** 「只會回梗圖的朋友」回應的一張梗圖。 */
export interface ChatMeme {
  meme_id: string;
  image_url: string;
  ocr_text: string | null;
  franchise: string | null;
}

export interface ChatReply {
  meme: ChatMeme | null;
  similarity: number | null;
  fallback: boolean;
}

/** 熱門梗圖榜一列（綜合熱度 = 讚×3 + 下載）。 */
export interface LeaderboardEntry {
  meme_id: string;
  image_url: string;
  ocr_text: string | null;
  franchise: string | null;
  likes: number;
  downloads: number;
  score: number;
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
