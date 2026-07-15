import {
  AlertTriangle,
  Camera,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Clock,
  Compass,
  Download,
  Flag,
  Flame,
  History as HistoryIcon,
  Loader2,
  Lock,
  LogIn,
  MessageCircle,
  Search,
  SearchX,
  Sparkles,
  SlidersHorizontal,
  Swords,
  ThumbsDown,
  ThumbsUp,
  Trophy,
  XCircle,
  Zap,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import MemeImage from "../components/MemeImage";
import {
  DEFAULT_PARAMS,
  fetchLeaderboard,
  fetchMeme,
  fetchMeta,
  fetchTask,
  fetchTaskHistory,
  imageUrl,
  logEvent,
  QuotaError,
  reportMeme,
  sendFeedback,
  submitTask,
  type TaskInput,
} from "../lib/api";
import BugReporter from "../components/BugReporter";
import ShareButton from "../components/ShareButton";
import { logBreadcrumb } from "../lib/breadcrumbs";
import { downscaleToBase64 } from "../lib/files";
import { navigate } from "../lib/router";
import {
  loadSettings,
  saveSettings,
  settingsToFilters,
  type UserSettings,
} from "../lib/settings";
import type {
  Filters,
  GalleryItem,
  LeaderboardEntry,
  Meta,
  Params,
  RecommendResponse,
  ResultItem,
  TaskDetail,
  TaskStatus,
  TaskSummary,
} from "../types";
import ChatScreen from "./ChatScreen";
import Chip, { toggle } from "./Chip";
import ExploreScreen from "./ExploreScreen";
import FavoritesScreen from "./FavoritesScreen";
import GalleryDetail from "./GalleryDetail";
import SettingsScreen from "./SettingsScreen";

type Tab = "home" | "chat" | "explore" | "history" | "settings";
type Mode = "screenshot" | "battle";
type Input = TaskInput;

const POLL_MS = 1800;
const RUNNING: TaskStatus[] = ["pending", "running"];

// 進行中的搜尋存 localStorage → 重整後續跑同一個任務（不重新搜、不歸零計時）。
// startedAt 用「本機送出時間」而非伺服器 created_at，避免裝置/伺服器時鐘差導致秒數歸零。
const ACTIVE_SEARCH_KEY = "memeradar.activeSearch";

function loadActiveSearch(): { taskId: string; startedAt: number } | null {
  try {
    const raw = localStorage.getItem(ACTIVE_SEARCH_KEY);
    if (!raw) return null;
    const v = JSON.parse(raw) as { taskId?: string; startedAt?: number };
    return v.taskId ? { taskId: v.taskId, startedAt: v.startedAt ?? 0 } : null;
  } catch {
    return null;
  }
}

function saveActiveSearch(taskId: string, startedAt: number): void {
  try {
    localStorage.setItem(ACTIVE_SEARCH_KEY, JSON.stringify({ taskId, startedAt }));
  } catch {
    /* localStorage 不可用 → 略過 */
  }
}

function clearActiveSearch(): void {
  try {
    localStorage.removeItem(ACTIVE_SEARCH_KEY);
  } catch {
    /* ignore */
  }
}

// 搜尋更多：拉高多樣性、放寬相似度門檻，換一批不同的圖
const REFINE_PARAMS: Params = { ...DEFAULT_PARAMS, diversity: 0.85, min_similarity: 0.3, candidate_k: 80 };

const EXT: Record<string, string> = {
  "image/png": "png",
  "image/jpeg": "jpg",
  "image/webp": "webp",
};

/** 存圖：iOS 用系統分享面板（含「儲存影像」直接存到照片），Android/桌機直接下載。 */
async function saveImage(url: string, name: string) {
  let blob: Blob;
  let file: File;
  try {
    const res = await fetch(url);
    blob = await res.blob();
    file = new File([blob], `${name}.${EXT[blob.type] ?? "png"}`, { type: blob.type });
  } catch {
    window.open(url, "_blank");
    return;
  }

  // iOS Safari：navigator.share 帶檔案 → 分享面板的「儲存影像」存進相簿
  const nav = navigator as Navigator & { canShare?: (data: ShareData) => boolean };
  if (typeof nav.share === "function" && nav.canShare?.({ files: [file] })) {
    try {
      await nav.share({ files: [file] });
    } catch {
      /* 使用者取消分享面板，不再退回下載 */
    }
    return;
  }

  // Android / 桌機：直接下載
  const objectUrl = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = objectUrl;
  a.download = file.name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(objectUrl);
}

export default function MobileApp({ initialMemeId }: { initialMemeId?: string | null } = {}) {
  const [tab, setTab] = useState<Tab>("home");
  const [settings, setSettings] = useState<UserSettings>(() => loadSettings());
  const [meta, setMeta] = useState<Meta | null>(null);
  const [showBoard, setShowBoard] = useState(false);
  const [headerHidden, setHeaderHidden] = useState(false);
  const [deepMeme, setDeepMeme] = useState<GalleryItem | null>(null); // 分享 deep-link 開的圖
  const [favoritesOpen, setFavoritesOpen] = useState(false); // 我的收藏覆蓋層
  const mainRef = useRef<HTMLElement>(null);

  // 分享連結 /m/{id} 進來 → 抓該圖、開 GalleryDetail
  useEffect(() => {
    if (!initialMemeId) return;
    let alive = true;
    fetchMeme(initialMemeId)
      .then((m) => alive && setDeepMeme(m))
      .catch(() => alive && navigate("/"));
    return () => {
      alive = false;
    };
  }, [initialMemeId]);
  const scrollState = useRef<{ y: number; target: EventTarget | null }>({ y: 0, target: null });

  // 非同步任務：送出得 task_id，背景執行，前台輪詢 fetchTask 直到 done/error。
  // 初始值自 localStorage 還原 → 重整後續跑上次的搜尋（進行中就繼續等、完成就顯示結果）。
  const [activeTaskId, setActiveTaskId] = useState<string | null>(() => loadActiveSearch()?.taskId ?? null);
  const [task, setTask] = useState<TaskDetail | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [homeError, setHomeError] = useState<string | null>(null);
  const [quota, setQuota] = useState<{ limit: number } | null>(null);
  const [battleImage, setBattleImage] = useState<string | null>(null);
  const [typing, setTyping] = useState(false);
  const [text, setText] = useState("");
  const modeRef = useRef<Mode>("screenshot");
  const fileRef = useRef<HTMLInputElement>(null);
  const lastInput = useRef<Input | null>(null);
  const startRef = useRef(loadActiveSearch()?.startedAt ?? 0); // 本機任務起算時間（ms）

  useEffect(() => {
    fetchMeta().then(setMeta).catch(() => {});
  }, []);

  // header 自動隱藏：往下滑收起（讓內容多一點空間）、往上滑或到頂顯示。
  // capture 監聽 main → 各分頁自己的捲動容器都收得到，不用逐一接線。
  useEffect(() => {
    const el = mainRef.current;
    if (!el) return;
    const onScroll = (e: Event) => {
      const t = e.target as HTMLElement;
      if (!t || typeof t.scrollTop !== "number") return;
      const st = scrollState.current;
      if (t !== st.target) st.target = t; // 換容器 → 重置基準
      const y = t.scrollTop;
      if (y < 8) setHeaderHidden(false);
      else if (y > st.y + 6) setHeaderHidden(true);
      else if (y < st.y - 6) setHeaderHidden(false);
      st.y = y;
    };
    el.addEventListener("scroll", onScroll, true);
    return () => el.removeEventListener("scroll", onScroll, true);
  }, []);

  // 換分頁一律先顯示 header
  useEffect(() => {
    setHeaderHidden(false);
    logBreadcrumb("nav", `分頁：${tab}`);
  }, [tab]);

  // 輪詢當前任務進度；任務完成/失敗即停。切到別的分頁也持續在背景輪詢。
  useEffect(() => {
    if (!activeTaskId) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      try {
        const t = await fetchTask(activeTaskId);
        if (!alive) return;
        // 輪詢在 done/error 後即停，故終態只會被抓到一次 → 在此留一筆麵包屑
        if (t.status === "done") {
          logBreadcrumb("result", `完成 ${t.result?.results?.length ?? 0} 張`, {
            fast: t.result?.debug?.fast?.source,
            ms: t.result?.debug?.timings_ms?.total,
          });
        } else if (t.status === "error") {
          logBreadcrumb("error", `任務失敗：${t.error ?? ""}`.slice(0, 120));
        }
        setTask(t);
        if (RUNNING.includes(t.status)) timer = setTimeout(tick, POLL_MS);
      } catch {
        if (alive) timer = setTimeout(tick, 3000); // 網路瞬斷：稍後續試
      }
    };
    void tick();
    return () => {
      alive = false;
      clearTimeout(timer);
    };
  }, [activeTaskId]);

  const updateSettings = (next: UserSettings) => {
    setSettings(next);
    saveSettings(next);
  };

  const submit = useCallback(
    async (input: Input, filters: Filters, params: Params, fast: boolean) => {
    lastInput.current = input;
    logBreadcrumb("action", `搜尋：${input.kind}${fast ? "（快速）" : "（精準）"}`);
    startRef.current = Date.now();
    setHomeError(null);
    setQuota(null);
    setTask(null);
    setActiveTaskId(null);
    setTab("home");
    setSubmitting(true);
    try {
      const { task_id } = await submitTask(input, filters, params, fast);
      setActiveTaskId(task_id); // 觸發輪詢
      saveActiveSearch(task_id, startRef.current); // 存起來 → 重整可續跑
    } catch (e) {
      if (e instanceof QuotaError) {
        setQuota({ limit: e.limit }); // 免費次數用完 → 引導登入
      } else {
        setHomeError(e instanceof Error ? e.message : "送出失敗，請再試一次");
      }
    } finally {
      setSubmitting(false);
    }
  }, []);

  // 從歷史開啟某任務：done 立刻顯示結果，仍在跑則接續輪詢
  const openTask = useCallback((id: string) => {
    startRef.current = 0; // 從歷史開啟：無本機起算時間 → 計時退回伺服器 created_at
    saveActiveSearch(id, 0);
    setHomeError(null);
    setBattleImage(null); // 歷史不留存對方梗圖
    setTab("home");
    setActiveTaskId((prev) => {
      if (prev !== id) setTask(null);
      return id;
    });
  }, []);

  const pick = (mode: Mode) => {
    modeRef.current = mode;
    fileRef.current?.click();
  };

  const onFile = useCallback(
    async (file: File | undefined) => {
      if (!file) return;
      // 上傳前先縮圖（原始像素高解析截圖會撐爆 gateway → 502）；縮完一律 JPEG
      const b64 = await downscaleToBase64(file);
      const filters = settingsToFilters(settings);
      if (modeRef.current === "battle") {
        // 梗圖大戰＝理解對方的梗圖（多半沒字），本質是視覺任務 → 一律走 VLM 精準
        setBattleImage(`data:image/jpeg;base64,${b64}`);
        void submit({ kind: "battle", image: b64 }, filters, DEFAULT_PARAMS, false);
      } else {
        setBattleImage(null);
        void submit({ kind: "screenshot", image: b64 }, filters, DEFAULT_PARAMS, settings.fastMode);
      }
    },
    [submit, settings],
  );

  const onText = useCallback(() => {
    const t = text.trim();
    if (!t) return;
    setBattleImage(null);
    void submit({ kind: "text", text: t }, settingsToFilters(settings), DEFAULT_PARAMS, settings.fastMode);
  }, [text, submit, settings]);

  // 搜尋更多：用選定的標籤 + 更高多樣性，重送上一個輸入（成為一筆新任務）
  const refine = (franchises: string[], categories: string[]) => {
    if (!lastInput.current) return;
    void submit(
      lastInput.current,
      { franchises, categories, exclude_nsfw: settings.excludeNsfw },
      REFINE_PARAMS,
      lastInput.current.kind === "battle" ? false : settings.fastMode,
    );
  };

  const reset = () => {
    clearActiveSearch();
    startRef.current = 0;
    setActiveTaskId(null);
    setTask(null);
    setHomeError(null);
    setQuota(null);
    setText("");
    setTyping(false);
    setBattleImage(null);
  };

  const done = task?.status === "done" && task.result ? task.result : null;
  const errorMsg = homeError ?? (task?.status === "error" ? task?.error : null);
  const loading = submitting || (activeTaskId !== null && !done && task?.status !== "error");
  const loadingBattle =
    task?.input_type === "meme_battle" || lastInput.current?.kind === "battle";
  // 已跑秒數的起算點：優先用「本機送出時間」（無時鐘差、跨分頁/重整都對），
  // 只有從歷史開啟、沒有本機起算時間時，才退回伺服器 created_at。
  const loadingStartMs =
    startRef.current || (task?.created_at ? Date.parse(task.created_at) : Date.now());

  let home;
  if (quota) {
    home = (
      <QuotaScreen
        limit={quota.limit}
        onLogin={() => {
          setQuota(null);
          setTab("settings");
        }}
        onReset={reset}
      />
    );
  } else if (loading) {
    home = <LoadingScreen battle={loadingBattle} startedAtMs={loadingStartMs} />;
  } else if (errorMsg) {
    home = (
      <div className="flex flex-1 flex-col items-center justify-center gap-4 px-8 text-center animate-fade-in">
        <p className="text-sm text-danger">{errorMsg}</p>
        <button
          onClick={reset}
          className="rounded-full border border-line px-5 py-2 text-sm text-fg active:bg-panel"
        >
          重新開始
        </button>
      </div>
    );
  } else if (done) {
    home = (
      <ResultsScreen
        response={done}
        battleImage={battleImage}
        meta={meta}
        initialTags={{ franchises: settings.franchises, categories: settings.categories }}
        onRefine={refine}
        onReset={reset}
      />
    );
  } else {
    home = (
      <IdleScreen
        typing={typing}
        text={text}
        onText={setText}
        onToggleTyping={() => setTyping((v) => !v)}
        onSubmitText={onText}
        onPick={pick}
        fastMode={settings.fastMode}
        onToggleFast={() => updateSettings({ ...settings, fastMode: !settings.fastMode })}
      />
    );
  }

  return (
    <div className="mx-auto flex min-h-[100dvh] max-w-md flex-col">
      <header
        className={`relative flex items-center justify-center gap-2 overflow-hidden px-4 transition-all duration-300 ${
          headerHidden && tab !== "explore"
            ? "max-h-0 py-0 opacity-0"
            : "max-h-28 pb-2 pt-[max(0.75rem,env(safe-area-inset-top))]"
        }`}
      >
        {tab === "explore" && (
          <button
            onClick={() => setTab("home")}
            className="absolute left-2 top-[max(0.5rem,env(safe-area-inset-top))] flex items-center gap-0.5 rounded-full px-2 py-1 text-sm text-muted active:bg-panel active:text-fg"
            aria-label="返回"
          >
            <ChevronLeft className="size-5" /> 返回
          </button>
        )}
        <span className="radar h-5 w-5 shrink-0" aria-hidden />
        <h1 className="font-mono text-sm font-semibold tracking-[0.35em]">
          {tab === "explore" ? "探索圖庫" : (
            <>
              MEME<span className="text-amber">RADAR</span>
            </>
          )}
        </h1>
        {tab !== "explore" && (
          <button
            onClick={() => setShowBoard(true)}
            className="absolute right-3 top-[max(0.55rem,env(safe-area-inset-top))] flex size-8 items-center justify-center rounded-full text-muted active:bg-panel active:text-amber"
            aria-label="梗圖風雲榜"
          >
            <Trophy className="size-5" strokeWidth={1.9} />
          </button>
        )}
      </header>

      <main ref={mainRef} className="flex min-h-0 flex-1 flex-col">
        {tab === "settings" ? (
          <SettingsScreen
          settings={settings}
          meta={meta}
          onChange={updateSettings}
          onOpenFavorites={() => setFavoritesOpen(true)}
        />
        ) : tab === "history" ? (
          <HistoryScreen activeId={activeTaskId} onOpen={openTask} />
        ) : tab === "explore" ? (
          <ExploreScreen />
        ) : tab === "chat" ? (
          <ChatScreen />
        ) : (
          home
        )}
      </main>

      {/* 探索圖庫沉浸式：隱藏底部導覽，改用左上「返回」（沿用你偏好的體驗） */}
      {tab !== "explore" && <NavBar tab={tab} onTab={setTab} running={loading} />}

      {showBoard && <LeaderboardModal onClose={() => setShowBoard(false)} />}

      {/* 分享 deep-link 開的梗圖 detail（覆蓋在最上層） */}
      {deepMeme && (
        <GalleryDetail
          item={deepMeme}
          onClose={() => {
            setDeepMeme(null);
            navigate("/");
          }}
        />
      )}

      {/* 我的收藏（登入使用者），全螢幕覆蓋 */}
      {favoritesOpen && <FavoritesScreen onClose={() => setFavoritesOpen(false)} />}

      {/* 浮動 bug 回報鈕：所有前台畫面都在，貼邊半透明不擋內容 */}
      <BugReporter />

      <input
        ref={fileRef}
        type="file"
        accept="image/png,image/jpeg,image/webp"
        className="hidden"
        onChange={(e) => {
          void onFile(e.target.files?.[0]);
          e.target.value = "";
        }}
      />
    </div>
  );
}

function NavBar({
  tab,
  onTab,
  running,
}: {
  tab: Tab;
  onTab: (t: Tab) => void;
  running: boolean;
}) {
  const items: Array<{ id: Tab; label: string; Icon: typeof Sparkles; busy?: boolean }> = [
    { id: "home", label: "推薦", Icon: Sparkles },
    { id: "chat", label: "梗友", Icon: MessageCircle },
    { id: "explore", label: "探索", Icon: Compass },
    { id: "history", label: "歷史", Icon: HistoryIcon, busy: running },
    { id: "settings", label: "設定", Icon: SlidersHorizontal },
  ];
  return (
    <nav className="flex border-t border-line bg-panel pb-[env(safe-area-inset-bottom)]">
      {items.map(({ id, label, Icon, busy }) => (
        <button
          key={id}
          onClick={() => onTab(id)}
          className={`relative flex flex-1 flex-col items-center gap-0.5 py-2.5 text-[11px] transition-colors ${
            tab === id ? "text-amber" : "text-muted"
          }`}
          aria-current={tab === id}
        >
          <Icon
            className={`size-5 transition-transform duration-200 ${tab === id ? "scale-110" : ""}`}
            strokeWidth={tab === id ? 2.2 : 1.75}
          />
          {label}
          {busy && tab !== id && (
            <span className="absolute right-[calc(50%-1.1rem)] top-1.5 size-2 animate-pulse rounded-full bg-amber" />
          )}
        </button>
      ))}
    </nav>
  );
}

function IdleScreen({
  typing,
  text,
  onText,
  onToggleTyping,
  onSubmitText,
  onPick,
  fastMode,
  onToggleFast,
}: {
  typing: boolean;
  text: string;
  onText: (v: string) => void;
  onToggleTyping: () => void;
  onSubmitText: () => void;
  onPick: (mode: Mode) => void;
  fastMode: boolean;
  onToggleFast: () => void;
}) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-6 px-6 pb-4">
      <div
        className="text-center animate-fade-in-up stagger"
        style={{ "--i": 0 } as React.CSSProperties}
      >
        <p className="text-lg font-semibold leading-relaxed">
          不知道怎麼回？
          <br />
          <span className="text-amber">丟給我，我幫你想梗圖。</span>
        </p>
      </div>

      <button
        onClick={() => onPick("screenshot")}
        style={{ "--i": 1 } as React.CSSProperties}
        className="flex w-full items-center gap-4 rounded-2xl border border-line bg-panel px-5 py-5
                   text-left transition-transform animate-fade-in-up stagger active:scale-[0.99] active:bg-raised"
      >
        <Camera className="size-7 shrink-0 text-amber" strokeWidth={1.75} />
        <span>
          <span className="block text-base font-semibold">上傳對話截圖</span>
          <span className="block text-xs text-muted">看對話內容，推薦怎麼回</span>
        </span>
      </button>

      <button
        onClick={() => onPick("battle")}
        style={{ "--i": 2 } as React.CSSProperties}
        className="flex w-full items-center gap-4 rounded-2xl border border-amber/50 bg-amber-soft
                   px-5 py-5 text-left transition-transform animate-fade-in-up stagger active:scale-[0.99] active:bg-amber/20"
      >
        <Swords className="size-7 shrink-0 text-amber" strokeWidth={1.75} />
        <span>
          <span className="block text-base font-semibold text-amber">對方丟了梗圖</span>
          <span className="block text-xs text-muted">梗圖大戰——上傳對方的圖，挑一張回敬</span>
        </span>
      </button>

      <button
        role="switch"
        aria-checked={fastMode}
        aria-label="快速模式"
        onClick={onToggleFast}
        style={{ "--i": 3 } as React.CSSProperties}
        className="flex w-full items-center gap-3 rounded-2xl border border-line bg-panel/60 px-5
                   py-3.5 text-left transition-transform animate-fade-in-up stagger active:scale-[0.99]"
      >
        <Zap
          className={`size-5 shrink-0 ${fastMode ? "text-amber" : "text-muted"}`}
          strokeWidth={2}
          fill={fastMode ? "currentColor" : "none"}
        />
        <span className="min-w-0 flex-1">
          <span className="block text-sm font-semibold">
            快速模式{fastMode ? "" : "（已關）"}
          </span>
          <span className="block text-xs text-muted">
            {fastMode ? "秒回，直接讀截圖文字（較粗略）" : "AI 精讀對話，較慢但更懂梗"}
          </span>
        </span>
        <span
          className={`relative h-6 w-10 shrink-0 rounded-full transition-colors ${
            fastMode ? "bg-amber" : "bg-line"
          }`}
        >
          <span
            className={`absolute top-0.5 size-5 rounded-full bg-white shadow transition-all ${
              fastMode ? "left-[1.125rem]" : "left-0.5"
            }`}
          />
        </span>
      </button>

      <div
        className="w-full animate-fade-in-up stagger"
        style={{ "--i": 4 } as React.CSSProperties}
      >
        <button
          onClick={onToggleTyping}
          className="mx-auto block text-xs text-muted underline underline-offset-4 active:text-fg"
        >
          {typing ? "收起" : "或手動輸入對方說的話"}
        </button>
        {typing && (
          <div className="mt-3 flex flex-col gap-2">
            <textarea
              value={text}
              onChange={(e) => onText(e.target.value)}
              rows={2}
              placeholder="例如：你報告怎麼又遲交了"
              className="w-full resize-none rounded-2xl border border-line bg-panel px-4 py-3
                         text-sm outline-none focus:border-amber"
            />
            <button
              onClick={onSubmitText}
              disabled={!text.trim()}
              className="rounded-full bg-amber py-3 text-sm font-semibold text-ink
                         disabled:opacity-40 active:opacity-80"
            >
              幫我想回應
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

/** 免費次數用完：引導登入解鎖無限（登入區在設定頁）。 */
function QuotaScreen({
  limit,
  onLogin,
  onReset,
}: {
  limit: number;
  onLogin: () => void;
  onReset: () => void;
}) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-5 px-8 text-center animate-fade-in">
      <div className="grid size-16 place-items-center rounded-full bg-amber-soft animate-scale-in">
        <Lock className="size-8 text-amber" strokeWidth={1.75} />
      </div>
      <div>
        <p className="text-base font-semibold">今天的免費次數用完了</p>
        <p className="mx-auto mt-1.5 max-w-[17rem] text-sm leading-relaxed text-muted">
          免費每天 {limit} 次。用 Google 登入即可
          <span className="text-amber">無限使用</span>，還能貢獻梗圖到大家的共用圖庫。
        </p>
      </div>
      <button
        onClick={onLogin}
        className="flex items-center gap-2 rounded-full bg-amber px-7 py-3 text-sm font-semibold text-ink active:opacity-80"
      >
        <LogIn className="size-4" /> 用 Google 登入
      </button>
      <button onClick={onReset} className="text-xs text-muted underline underline-offset-4">
        明天再來
      </button>
    </div>
  );
}

const LOADING_STAGES = {
  battle: ["讀取對方的梗圖……", "解讀它想表達什麼……", "翻你的梗圖庫……", "挑一張最嗆的回敬……"],
  normal: ["讀取對話……", "解讀對方情緒……", "掃描梗圖庫……", "排出最貼的幾張……"],
};

// 等待時輪播的幽默台詞（梗圖口吻，隨機出）
const LOADING_QUIPS = [
  "免費仔的宿命，稍等一下下 🙏",
  "正在你的梗圖庫裡翻箱倒櫃……",
  "思考要多嗆你朋友中……",
  "梗圖們正在排隊試鏡 🎬",
  "正在計算最大傷害輸出 💥",
  "已讀不回的藝術，交給我 🫡",
  "翻遍整庫只為那一張……",
  "誠意十足，網速盡力 🛜",
  "醞釀一個不失禮又到位的回擊……",
  "AI 也想給你最嗆的那張 😤",
  "正在偷看你朋友會不會森 77……",
  "梗圖挑選中，品味需要時間 💅",
];

function pickQuip(): string {
  return LOADING_QUIPS[Math.floor(Math.random() * LOADING_QUIPS.length)];
}

/** startedAtMs：任務起算時間（毫秒 epoch）。以它算已跑秒數 → 切到其他分頁再回來也不會歸零。 */
function LoadingScreen({ battle, startedAtMs }: { battle: boolean; startedAtMs: number }) {
  const stages = battle ? LOADING_STAGES.battle : LOADING_STAGES.normal;
  const [now, setNow] = useState(() => Date.now());
  const [quip, setQuip] = useState(() => pickQuip());

  useEffect(() => {
    const clock = setInterval(() => setNow(Date.now()), 1000);
    const rotate = setInterval(() => setQuip(pickQuip()), 2600);
    return () => {
      clearInterval(clock);
      clearInterval(rotate);
    };
  }, []);

  const secs = Math.max(0, Math.floor((now - startedAtMs) / 1000));
  const step = Math.min(Math.floor(secs / 3), stages.length - 1);

  return (
    <div
      className="flex flex-1 flex-col items-center justify-center gap-6 px-8 text-center animate-fade-in"
      role="status"
      aria-live="polite"
    >
      <div className="radar h-36 w-36">
        <span className="radar-blip" style={{ left: "62%", top: "30%" }} />
        <span className="radar-blip" style={{ left: "30%", top: "58%", animationDelay: "0.9s" }} />
        <span className="radar-blip" style={{ left: "52%", top: "70%", animationDelay: "1.6s" }} />
      </div>

      {/* 輪播幽默台詞（key 換 → 每句淡入） */}
      <p key={quip} className="min-h-[2.5rem] max-w-[17rem] text-sm font-medium text-amber animate-fade-in">
        {quip}
      </p>

      <div className="flex flex-col items-center gap-2.5">
        <p className="text-xs text-muted transition-all">{stages[step]}</p>
        <div className="flex items-center gap-1.5">
          {stages.map((_, i) => (
            <span
              key={i}
              className={`h-1.5 rounded-full transition-all duration-500 ${
                i <= step ? "w-5 bg-amber" : "w-1.5 bg-line"
              }`}
            />
          ))}
        </div>
        <p className="font-mono text-xs text-muted">已跑 {secs} 秒</p>
      </div>

      <p className="max-w-[16rem] text-xs leading-relaxed text-muted">
        免費模型有時要想久一點。可以先去別的地方逛逛，
        <span className="text-fg">好了會出現在「歷史」</span>。
      </p>
    </div>
  );
}

const STATUS_META: Record<TaskStatus, { label: string; Icon: typeof Clock; cls: string }> = {
  pending: { label: "排隊中", Icon: Clock, cls: "text-muted" },
  running: { label: "運算中", Icon: Loader2, cls: "text-amber" },
  done: { label: "完成", Icon: CheckCircle2, cls: "text-signal" },
  error: { label: "失敗", Icon: XCircle, cls: "text-danger" },
};

const INPUT_LABEL: Record<TaskSummary["input_type"], string> = {
  text: "文字",
  screenshot: "截圖",
  meme_battle: "梗圖大戰",
};

function timeAgo(iso: string): string {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const mins = Math.floor((Date.now() - then) / 60000);
  if (mins < 1) return "剛剛";
  if (mins < 60) return `${mins} 分鐘前`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs} 小時前`;
  return `${Math.floor(hrs / 24)} 天前`;
}

function HistoryScreen({
  activeId,
  onOpen,
}: {
  activeId: string | null;
  onOpen: (id: string) => void;
}) {
  const [items, setItems] = useState<TaskSummary[] | null>(null);

  const load = useCallback(() => {
    fetchTaskHistory()
      .then(setItems)
      .catch(() => setItems([]));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // 有任務仍在跑 → 定時刷新，讓進度會動
  useEffect(() => {
    if (!items?.some((t) => RUNNING.includes(t.status))) return;
    const timer = setInterval(load, 3000);
    return () => clearInterval(timer);
  }, [items, load]);

  if (items === null) {
    return (
      <div className="flex flex-1 items-center justify-center">
        <Loader2 className="size-6 animate-spin text-muted" />
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 px-8 text-center">
        <HistoryIcon className="size-9 text-muted" strokeWidth={1.5} />
        <p className="text-sm">還沒有任務</p>
        <p className="text-xs text-muted">回「推薦」丟一段對話或截圖，這裡會留下紀錄。</p>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto px-4 pb-4">
      <p className="px-1 py-3 text-xs text-muted">你的任務紀錄（存在這台裝置上）</p>
      <ul className="flex flex-col gap-2">
        {items.map((t) => {
          const s = STATUS_META[t.status];
          const active = t.task_id === activeId;
          return (
            <li key={t.task_id}>
              <button
                onClick={() => onOpen(t.task_id)}
                className={`flex w-full items-center gap-3 rounded-2xl border px-4 py-3 text-left active:bg-raised ${
                  active ? "border-amber/60 bg-amber-soft" : "border-line bg-panel"
                }`}
              >
                <s.Icon
                  className={`size-5 shrink-0 ${s.cls} ${t.status === "running" ? "animate-spin" : ""}`}
                  strokeWidth={1.9}
                />
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-sm text-fg">{t.label || "對話"}</span>
                  <span className="mt-0.5 block text-xs text-muted">
                    {INPUT_LABEL[t.input_type]} · {s.label} · {timeAgo(t.created_at)}
                  </span>
                </span>
                <ChevronRight className="size-4 shrink-0 text-muted" />
              </button>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

function ResultsScreen({
  response,
  battleImage,
  meta,
  initialTags,
  onRefine,
  onReset,
}: {
  response: RecommendResponse;
  battleImage: string | null;
  meta: Meta | null;
  initialTags: { franchises: string[]; categories: string[] };
  onRefine: (franchises: string[], categories: string[]) => void;
  onReset: () => void;
}) {
  const [index, setIndex] = useState(0);
  const [detail, setDetail] = useState<ResultItem | null>(null);
  const [refining, setRefining] = useState(false);
  const results = response.results;
  const scrollRef = useRef<HTMLDivElement>(null);

  const onScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget;
    setIndex(Math.round(el.scrollLeft / el.clientWidth));
  };

  // 點點 / 箭頭跳到指定張（滑鼠也能換，不只靠觸控滑動）
  const goTo = (i: number) => {
    const el = scrollRef.current;
    if (!el) return;
    const to = Math.max(0, Math.min(results.length - 1, i));
    el.scrollTo({ left: to * el.clientWidth, behavior: "smooth" });
  };

  if (results.length === 0) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-4 px-8 text-center animate-fade-in">
        <SearchX className="size-10 text-muted" strokeWidth={1.5} />
        <p className="text-sm">這次沒找到夠合適的梗圖</p>
        <p className="text-xs text-muted">
          {response.intent.sensitive ? "偵測到敏感情境，已保守處理" : "換個方向再找找"}
        </p>
        <button
          onClick={() => setRefining(true)}
          className="mt-2 flex items-center gap-2 rounded-full bg-amber px-6 py-2.5 text-sm font-semibold text-ink active:opacity-80"
        >
          <Search className="size-4" /> 換方向搜尋
        </button>
        <button onClick={onReset} className="text-xs text-muted underline underline-offset-4">
          搜尋下一張圖
        </button>
        {refining && (
          <RefineSheet
            meta={meta}
            initial={initialTags}
            onSearch={(f, c) => {
              setRefining(false);
              onRefine(f, c);
            }}
            onClose={() => setRefining(false)}
          />
        )}
      </div>
    );
  }

  return (
    <div className="flex flex-1 flex-col animate-fade-in">
      {response.intent.sensitive && (
        <p className="mx-4 mb-1 flex items-center justify-center gap-1.5 rounded-full bg-amber-soft px-3 py-1.5 text-center text-xs text-amber">
          <AlertTriangle className="size-3.5" /> 偵測到敏感情境，回應已降級為安撫
        </p>
      )}

      {battleImage && (
        <div className="mx-4 mb-1 flex items-center gap-2 rounded-2xl border border-line bg-panel p-2">
          <img src={battleImage} alt="對方丟的梗圖" className="h-12 w-12 rounded-lg object-cover" />
          <span className="text-xs text-muted">
            對方出這張 —— <span className="text-fg">往下滑挑一張回敬</span>
          </span>
        </div>
      )}

      <div
        ref={scrollRef}
        onScroll={onScroll}
        className="flex flex-1 snap-x snap-mandatory overflow-x-auto scroll-smooth"
        style={{ scrollbarWidth: "none" }}
      >
        {results.map((item) => (
          <Slide
            key={item.meme_id}
            item={item}
            queryId={response.query_id}
            onDetail={() => setDetail(item)}
          />
        ))}
      </div>

      <div className="flex items-center justify-center gap-3 py-2.5">
        <button
          onClick={() => goTo(index - 1)}
          disabled={index === 0}
          className="grid size-7 place-items-center rounded-full border border-line text-muted
                     transition-colors active:bg-panel disabled:opacity-30"
          aria-label="上一張"
        >
          <ChevronLeft className="size-4" />
        </button>
        <div className="flex items-center gap-1.5">
          {results.map((item, i) => (
            <button
              key={item.meme_id}
              onClick={() => goTo(i)}
              className="p-1"
              aria-label={`第 ${i + 1} 張`}
              aria-current={i === index}
            >
              <span
                className={`block h-1.5 rounded-full transition-all ${
                  i === index ? "w-5 bg-amber" : "w-1.5 bg-line"
                }`}
              />
            </button>
          ))}
        </div>
        <button
          onClick={() => goTo(index + 1)}
          disabled={index === results.length - 1}
          className="grid size-7 place-items-center rounded-full border border-line text-muted
                     transition-colors active:bg-panel disabled:opacity-30"
          aria-label="下一張"
        >
          <ChevronRight className="size-4" />
        </button>
      </div>

      <div className="space-y-2 px-4 pb-3">
        <button
          onClick={() => setRefining(true)}
          className="flex w-full items-center justify-center gap-2 rounded-full bg-amber py-3 text-sm font-semibold text-ink active:opacity-80"
        >
          <Search className="size-4" /> 都不喜歡？搜尋更多
        </button>
        <button
          onClick={onReset}
          className="flex w-full items-center justify-center gap-2 rounded-full border border-line py-2.5 text-xs text-muted active:bg-panel"
        >
          <Camera className="size-3.5" /> 搜尋下一張圖
        </button>
      </div>

      {detail && (
        <DetailSheet
          item={detail}
          intentSummary={response.intent.summary}
          onClose={() => setDetail(null)}
        />
      )}
      {refining && (
        <RefineSheet
          meta={meta}
          initial={initialTags}
          onSearch={(f, c) => {
            setRefining(false);
            onRefine(f, c);
          }}
          onClose={() => setRefining(false)}
        />
      )}
    </div>
  );
}

/** 搜尋更多引導：選梗圖包 / 分類標籤，換一批。 */
function RefineSheet({
  meta,
  initial,
  onSearch,
  onClose,
}: {
  meta: Meta | null;
  initial: { franchises: string[]; categories: string[] };
  onSearch: (franchises: string[], categories: string[]) => void;
  onClose: () => void;
}) {
  const [franchises, setFranchises] = useState<string[]>(initial.franchises);
  const [categories, setCategories] = useState<string[]>(initial.categories);

  return (
    <div className="fixed inset-0 z-20 flex items-end bg-ink/70 animate-fade-in" onClick={onClose}>
      <div
        onClick={(e) => e.stopPropagation()}
        className="max-h-[80dvh] w-full overflow-y-auto rounded-t-3xl border-t border-line bg-panel px-5
                   animate-sheet-up pb-[max(1.5rem,env(safe-area-inset-bottom))] pt-3"
      >
        <div className="mx-auto mb-4 h-1 w-10 rounded-full bg-line" />
        <p className="mb-1 text-base font-semibold">想看哪種？</p>
        <p className="mb-4 text-xs text-muted">選幾個方向，我換一批給你（不選就換個更多樣的一批）。</p>

        <p className="mb-2 text-sm font-semibold">梗圖包</p>
        <div className="mb-4 flex flex-wrap gap-2">
          {meta?.franchises.map((f) => (
            <Chip
              key={f.name}
              label={f.name}
              active={franchises.includes(f.name)}
              onToggle={() => setFranchises((v) => toggle(v, f.name))}
            />
          ))}
        </div>

        <p className="mb-2 text-sm font-semibold">分類</p>
        <div className="mb-5 flex flex-wrap gap-2">
          {meta?.categories.map((c) => (
            <Chip
              key={c}
              label={c}
              active={categories.includes(c)}
              onToggle={() => setCategories((v) => toggle(v, c))}
            />
          ))}
        </div>

        <button
          onClick={() => onSearch(franchises, categories)}
          className="flex w-full items-center justify-center gap-2 rounded-full bg-amber py-3 text-sm font-semibold text-ink active:opacity-80"
        >
          <Search className="size-4" /> 搜尋
        </button>
      </div>
    </div>
  );
}

function Slide({
  item,
  queryId,
  onDetail,
}: {
  item: ResultItem;
  queryId: string;
  onDetail: () => void;
}) {
  const [sent, setSent] = useState<"up" | "down" | null>(null);

  const rate = (rating: "up" | "down") => {
    const next = sent === rating ? null : rating;
    setSent(next);
    if (next) {
      void sendFeedback({
        query_id: queryId,
        meme_id: item.meme_id,
        rank: item.rank,
        rating: next,
      }).catch(() => {});
    }
  };

  return (
    <section className="flex w-full shrink-0 snap-center flex-col px-4">
      <div className="relative flex flex-1 items-center justify-center rounded-3xl border border-line bg-ink">
        <MemeImage
          src={item.image_url}
          alt={`推薦梗圖第 ${item.rank} 名`}
          className="max-h-[48vh] w-full rounded-3xl object-contain"
        />
        <span className="absolute left-3 top-3 rounded-full bg-ink/80 px-2.5 py-0.5 font-mono text-xs text-amber">
          #{item.rank}
        </span>
        <span className="absolute right-3 top-3 rounded-full border border-amber/70 bg-ink/70 px-2.5 py-0.5 text-xs text-amber">
          {item.matched_strategy}
        </span>
        <button
          onClick={() => {
            logEvent("download", { memeId: item.meme_id, meta: { src: "mobile", rank: item.rank } });
            void saveImage(imageUrl(item.image_url), `memeradar-${item.meme_id.slice(2, 10)}`);
          }}
          className="absolute bottom-3 right-3 flex items-center gap-1.5 rounded-full bg-ink/80 px-3 py-1.5 text-xs text-fg active:bg-ink"
          aria-label="儲存這張梗圖"
        >
          <Download className="size-4" strokeWidth={1.75} /> 存圖
        </button>
        <ShareButton
          memeId={item.meme_id}
          label
          className="absolute bottom-3 left-3 flex items-center gap-1.5 rounded-full bg-ink/80 px-3 py-1.5 text-xs text-fg active:bg-ink"
        />
      </div>

      <div className="mt-3 flex items-center gap-2">
        <button
          onClick={() => rate("up")}
          className={`flex flex-1 items-center justify-center rounded-full border py-3 active:scale-95 ${
            sent === "up" ? "border-signal bg-signal/15 text-signal" : "border-line text-fg"
          }`}
          aria-label="這張讚"
          aria-pressed={sent === "up"}
        >
          <ThumbsUp
            className={`size-5 ${sent === "up" ? "animate-pop" : ""}`}
            strokeWidth={sent === "up" ? 2.4 : 1.75}
          />
        </button>
        <button
          onClick={() => rate("down")}
          className={`flex flex-1 items-center justify-center rounded-full border py-3 active:scale-95 ${
            sent === "down" ? "border-danger bg-danger/15 text-danger" : "border-line text-fg"
          }`}
          aria-label="這張不行"
          aria-pressed={sent === "down"}
        >
          <ThumbsDown
            className={`size-5 ${sent === "down" ? "animate-pop" : ""}`}
            strokeWidth={sent === "down" ? 2.4 : 1.75}
          />
        </button>
        <button
          onClick={onDetail}
          className="rounded-full border border-line px-5 py-3 text-sm text-muted active:bg-panel"
        >
          詳細
        </button>
      </div>
    </section>
  );
}

function DetailSheet({
  item,
  intentSummary,
  onClose,
}: {
  item: ResultItem;
  intentSummary: string;
  onClose: () => void;
}) {
  const [reported, setReported] = useState(false);
  return (
    <div className="fixed inset-0 z-20 flex items-end bg-ink/70 animate-fade-in" onClick={onClose}>
      <div
        onClick={(e) => e.stopPropagation()}
        className="w-full rounded-t-3xl border-t border-line bg-panel px-5 animate-sheet-up
                   pb-[max(1.5rem,env(safe-area-inset-bottom))] pt-3"
      >
        <div className="mx-auto mb-4 h-1 w-10 rounded-full bg-line" />

        <div className="mb-3 flex items-center gap-2">
          <span className="rounded-full border border-amber px-2.5 py-0.5 text-xs text-amber">
            {item.matched_strategy}
          </span>
          {item.matched_tags.slice(0, 3).map((tag) => (
            <span key={tag} className="rounded-full bg-raised px-2 py-0.5 text-xs text-muted">
              {tag}
            </span>
          ))}
        </div>

        <p className="text-sm leading-relaxed">{item.reason}</p>

        {intentSummary && <p className="mt-2 text-xs text-muted">系統判讀的情境：{intentSummary}</p>}

        <div className="mt-4 space-y-2">
          <ScoreBar label="相似度" value={item.scores.vector} />
          <ScoreBar label="重排分" value={item.scores.rerank} />
          <ScoreBar label="最終分" value={item.scores.final} accent />
        </div>

        <button
          onClick={onClose}
          className="mt-5 w-full rounded-full bg-amber py-3 text-sm font-semibold text-ink active:opacity-80"
        >
          關閉
        </button>

        {reported ? (
          <p className="mt-3 text-center text-xs text-muted animate-fade-in">已收到你的回報，謝謝 🙏</p>
        ) : (
          <button
            onClick={() => {
              setReported(true);
              void reportMeme(item.meme_id);
            }}
            className="mt-3 flex w-full items-center justify-center gap-1.5 text-xs text-muted active:text-danger"
          >
            <Flag className="size-3.5" /> 檢舉這張不宜
          </button>
        )}
      </div>
    </div>
  );
}

function ScoreBar({ label, value, accent }: { label: string; value: number; accent?: boolean }) {
  return (
    <div className="flex items-center gap-3">
      <span className="w-14 shrink-0 text-xs text-muted">{label}</span>
      <div className="h-2 flex-1 overflow-hidden rounded-full bg-raised">
        <div
          className={`h-full rounded-full ${accent ? "bg-amber" : "bg-amber/50"}`}
          style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }}
        />
      </div>
      <span className="w-9 text-right font-mono text-xs">{value.toFixed(2)}</span>
    </div>
  );
}

const MEDALS = ["🥇", "🥈", "🥉"];

/** 梗圖風雲榜：小彩蛋，點頭上的獎盃跳出。綜合熱度 = 讚×3 + 下載。 */
function LeaderboardModal({ onClose }: { onClose: () => void }) {
  const [rows, setRows] = useState<LeaderboardEntry[] | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    fetchLeaderboard(20)
      .then(setRows)
      .catch(() => setFailed(true));
  }, []);

  return (
    <div className="fixed inset-0 z-30 flex items-end bg-ink/70 animate-fade-in" onClick={onClose}>
      <div
        onClick={(e) => e.stopPropagation()}
        className="max-h-[82dvh] w-full overflow-y-auto rounded-t-3xl border-t border-amber/40 bg-panel
                   animate-sheet-up px-5 pb-[max(1.5rem,env(safe-area-inset-bottom))] pt-3"
      >
        <div className="mx-auto mb-4 h-1 w-10 rounded-full bg-line" />
        <div className="mb-1 flex items-center gap-2">
          <Trophy className="size-5 text-amber" strokeWidth={1.9} />
          <p className="text-base font-semibold">梗圖風雲榜</p>
        </div>
        <p className="mb-4 text-xs text-muted">大家最愛存、最常按讚的幾張（讚 ×3 ＋ 下載）。</p>

        {failed ? (
          <EmptyBoard text="榜單暫時拿不到，晚點再回來看看。" />
        ) : rows === null ? (
          <div className="flex justify-center py-12">
            <Loader2 className="size-6 animate-spin text-muted" />
          </div>
        ) : rows.length === 0 ? (
          <EmptyBoard text="還沒有人互動——去存幾張、按幾個讚，這裡就熱鬧起來了。" />
        ) : (
          <>
            <ol className="flex flex-col gap-2">
              {rows.map((row, i) => (
                <LeaderRow key={row.meme_id} row={row} place={i + 1} index={i} />
              ))}
            </ol>
            {rows.length < 3 && (
              <p className="mt-3 text-center text-xs text-muted">榜單剛起步，多互動就會長出更多名次。</p>
            )}
          </>
        )}

        <button
          onClick={onClose}
          className="mt-5 w-full rounded-full border border-line py-2.5 text-sm text-muted active:bg-raised"
        >
          關閉
        </button>
      </div>
    </div>
  );
}

function LeaderRow({ row, place, index }: { row: LeaderboardEntry; place: number; index: number }) {
  const medal = MEDALS[place - 1];
  const top = place <= 3;
  return (
    <li
      style={{ "--i": index } as React.CSSProperties}
      className={`flex items-center gap-3 rounded-2xl border px-3 py-2.5 animate-fade-in-up stagger ${
        top ? "border-amber/50 bg-amber-soft" : "border-line bg-raised"
      }`}
    >
      <span
        className={`w-7 shrink-0 text-center ${
          medal ? "text-xl" : "font-mono text-sm text-muted"
        }`}
      >
        {medal ?? place}
      </span>
      <MemeImage
        src={row.image_url}
        alt={row.ocr_text ?? "梗圖"}
        className="size-12 shrink-0 rounded-lg object-cover"
      />
      <div className="min-w-0 flex-1">
        <p className="truncate text-sm text-fg">
          {row.ocr_text?.trim() || row.franchise || "梗圖"}
        </p>
        {row.franchise && (
          <p className="mt-0.5 truncate text-xs text-muted">{row.franchise}</p>
        )}
      </div>
      <div className="flex shrink-0 flex-col items-end">
        <span className="flex items-center gap-1 font-mono text-sm font-semibold text-amber">
          <Flame className="size-3.5" strokeWidth={2} /> {row.score}
        </span>
        <span className="mt-0.5 flex items-center gap-2 text-[11px] text-muted">
          <span className="flex items-center gap-0.5">
            <ThumbsUp className="size-3" /> {row.likes}
          </span>
          <span className="flex items-center gap-0.5">
            <Download className="size-3" /> {row.downloads}
          </span>
        </span>
      </div>
    </li>
  );
}

function EmptyBoard({ text }: { text: string }) {
  return (
    <div className="flex flex-col items-center gap-3 px-6 py-10 text-center">
      <Trophy className="size-9 text-line" strokeWidth={1.5} />
      <p className="text-sm text-muted">{text}</p>
    </div>
  );
}
