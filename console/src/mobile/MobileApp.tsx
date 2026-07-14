import {
  AlertTriangle,
  Camera,
  CheckCircle2,
  ChevronRight,
  Clock,
  Download,
  Flag,
  Flame,
  History as HistoryIcon,
  Loader2,
  Lock,
  LogIn,
  RotateCcw,
  Search,
  SearchX,
  Sparkles,
  SlidersHorizontal,
  Swords,
  ThumbsDown,
  ThumbsUp,
  Trophy,
  XCircle,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import MemeImage from "../components/MemeImage";
import {
  DEFAULT_PARAMS,
  fetchLeaderboard,
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
import { fileToBase64 } from "../lib/files";
import {
  loadSettings,
  saveSettings,
  settingsToFilters,
  type UserSettings,
} from "../lib/settings";
import type {
  Filters,
  LeaderboardEntry,
  Meta,
  Params,
  RecommendResponse,
  ResultItem,
  TaskDetail,
  TaskStatus,
  TaskSummary,
} from "../types";
import Chip, { toggle } from "./Chip";
import SettingsScreen from "./SettingsScreen";

type Tab = "home" | "history" | "settings";
type Mode = "screenshot" | "battle";
type Input = TaskInput;

const POLL_MS = 1800;
const RUNNING: TaskStatus[] = ["pending", "running"];

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

export default function MobileApp() {
  const [tab, setTab] = useState<Tab>("home");
  const [settings, setSettings] = useState<UserSettings>(() => loadSettings());
  const [meta, setMeta] = useState<Meta | null>(null);
  const [showBoard, setShowBoard] = useState(false);

  // 非同步任務：送出得 task_id，背景執行，前台輪詢 fetchTask 直到 done/error。
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null);
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

  useEffect(() => {
    fetchMeta().then(setMeta).catch(() => {});
  }, []);

  // 輪詢當前任務進度；任務完成/失敗即停。切到別的分頁也持續在背景輪詢。
  useEffect(() => {
    if (!activeTaskId) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      try {
        const t = await fetchTask(activeTaskId);
        if (!alive) return;
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

  const submit = useCallback(async (input: Input, filters: Filters, params: Params) => {
    lastInput.current = input;
    setHomeError(null);
    setQuota(null);
    setTask(null);
    setActiveTaskId(null);
    setTab("home");
    setSubmitting(true);
    try {
      const { task_id } = await submitTask(input, filters, params);
      setActiveTaskId(task_id); // 觸發輪詢
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
      const b64 = await fileToBase64(file);
      const filters = settingsToFilters(settings);
      if (modeRef.current === "battle") {
        setBattleImage(`data:${file.type};base64,${b64}`);
        void submit({ kind: "battle", image: b64 }, filters, DEFAULT_PARAMS);
      } else {
        setBattleImage(null);
        void submit({ kind: "screenshot", image: b64 }, filters, DEFAULT_PARAMS);
      }
    },
    [submit, settings],
  );

  const onText = useCallback(() => {
    const t = text.trim();
    if (!t) return;
    setBattleImage(null);
    void submit({ kind: "text", text: t }, settingsToFilters(settings), DEFAULT_PARAMS);
  }, [text, submit, settings]);

  // 搜尋更多：用選定的標籤 + 更高多樣性，重送上一個輸入（成為一筆新任務）
  const refine = (franchises: string[], categories: string[]) => {
    if (!lastInput.current) return;
    void submit(
      lastInput.current,
      { franchises, categories, exclude_nsfw: settings.excludeNsfw },
      REFINE_PARAMS,
    );
  };

  const reset = () => {
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
    home = <LoadingScreen battle={loadingBattle} />;
  } else if (errorMsg) {
    home = (
      <div className="flex flex-1 flex-col items-center justify-center gap-4 px-8 text-center">
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
      />
    );
  }

  return (
    <div className="mx-auto flex min-h-[100dvh] max-w-md flex-col">
      <header className="relative flex items-center justify-center gap-2 px-4 pb-2 pt-[max(0.75rem,env(safe-area-inset-top))]">
        <span className="radar h-5 w-5 shrink-0" aria-hidden />
        <h1 className="font-mono text-sm font-semibold tracking-[0.35em]">
          MEME<span className="text-amber">RADAR</span>
        </h1>
        <button
          onClick={() => setShowBoard(true)}
          className="absolute right-3 top-[max(0.55rem,env(safe-area-inset-top))] flex size-8 items-center justify-center rounded-full text-muted active:bg-panel active:text-amber"
          aria-label="梗圖風雲榜"
        >
          <Trophy className="size-5" strokeWidth={1.9} />
        </button>
      </header>

      <main className="flex min-h-0 flex-1 flex-col">
        {tab === "settings" ? (
          <SettingsScreen settings={settings} meta={meta} onChange={updateSettings} />
        ) : tab === "history" ? (
          <HistoryScreen activeId={activeTaskId} onOpen={openTask} />
        ) : (
          home
        )}
      </main>

      <NavBar tab={tab} onTab={setTab} running={loading} />

      {showBoard && <LeaderboardModal onClose={() => setShowBoard(false)} />}

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
    { id: "history", label: "歷史", Icon: HistoryIcon, busy: running },
    { id: "settings", label: "設定", Icon: SlidersHorizontal },
  ];
  return (
    <nav className="flex border-t border-line bg-panel pb-[env(safe-area-inset-bottom)]">
      {items.map(({ id, label, Icon, busy }) => (
        <button
          key={id}
          onClick={() => onTab(id)}
          className={`relative flex flex-1 flex-col items-center gap-0.5 py-2.5 text-[11px] ${
            tab === id ? "text-amber" : "text-muted"
          }`}
          aria-current={tab === id}
        >
          <Icon className="size-5" strokeWidth={tab === id ? 2.2 : 1.75} />
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
}: {
  typing: boolean;
  text: string;
  onText: (v: string) => void;
  onToggleTyping: () => void;
  onSubmitText: () => void;
  onPick: (mode: Mode) => void;
}) {
  return (
    <div className="flex flex-1 flex-col items-center justify-center gap-6 px-6 pb-4">
      <div className="text-center">
        <p className="text-lg font-semibold leading-relaxed">
          不知道怎麼回？
          <br />
          <span className="text-amber">丟給我，我幫你想梗圖。</span>
        </p>
      </div>

      <button
        onClick={() => onPick("screenshot")}
        className="flex w-full items-center gap-4 rounded-2xl border border-line bg-panel px-5 py-5
                   text-left active:scale-[0.99] active:bg-raised"
      >
        <Camera className="size-7 shrink-0 text-amber" strokeWidth={1.75} />
        <span>
          <span className="block text-base font-semibold">上傳對話截圖</span>
          <span className="block text-xs text-muted">看對話內容，推薦怎麼回</span>
        </span>
      </button>

      <button
        onClick={() => onPick("battle")}
        className="flex w-full items-center gap-4 rounded-2xl border border-amber/50 bg-amber-soft
                   px-5 py-5 text-left active:scale-[0.99] active:bg-amber/20"
      >
        <Swords className="size-7 shrink-0 text-amber" strokeWidth={1.75} />
        <span>
          <span className="block text-base font-semibold text-amber">對方丟了梗圖</span>
          <span className="block text-xs text-muted">梗圖大戰——上傳對方的圖，挑一張回敬</span>
        </span>
      </button>

      <div className="w-full">
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
    <div className="flex flex-1 flex-col items-center justify-center gap-5 px-8 text-center">
      <div className="grid size-16 place-items-center rounded-full bg-amber-soft">
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

function LoadingScreen({ battle }: { battle: boolean }) {
  const stages = battle ? LOADING_STAGES.battle : LOADING_STAGES.normal;
  const [step, setStep] = useState(0);
  const [secs, setSecs] = useState(0);

  useEffect(() => {
    setStep(0);
    setSecs(0);
    const advance = setInterval(
      () => setStep((s) => Math.min(s + 1, stages.length - 1)),
      2600,
    );
    const clock = setInterval(() => setSecs((s) => s + 1), 1000);
    return () => {
      clearInterval(advance);
      clearInterval(clock);
    };
  }, [stages.length]);

  return (
    <div
      className="flex flex-1 flex-col items-center justify-center gap-7 px-8 text-center"
      role="status"
      aria-live="polite"
    >
      <div className="radar h-36 w-36">
        <span className="radar-blip" style={{ left: "62%", top: "30%" }} />
        <span className="radar-blip" style={{ left: "30%", top: "58%", animationDelay: "0.9s" }} />
        <span className="radar-blip" style={{ left: "52%", top: "70%", animationDelay: "1.6s" }} />
      </div>

      <div className="flex flex-col items-center gap-3">
        <p className="min-h-[1.5rem] text-sm font-medium text-fg transition-all">
          {stages[step]}
        </p>
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

  const onScroll = (e: React.UIEvent<HTMLDivElement>) => {
    const el = e.currentTarget;
    setIndex(Math.round(el.scrollLeft / el.clientWidth));
  };

  if (results.length === 0) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-4 px-8 text-center">
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
          重新上傳
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
    <div className="flex flex-1 flex-col">
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

      <div className="flex items-center justify-center gap-1.5 py-2.5">
        {results.map((item, i) => (
          <span
            key={item.meme_id}
            className={`h-1.5 rounded-full transition-all ${
              i === index ? "w-5 bg-amber" : "w-1.5 bg-line"
            }`}
          />
        ))}
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
          <RotateCcw className="size-3.5" /> 重新上傳
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
    <div className="fixed inset-0 z-20 flex items-end bg-ink/70" onClick={onClose}>
      <div
        onClick={(e) => e.stopPropagation()}
        className="max-h-[80dvh] w-full overflow-y-auto rounded-t-3xl border-t border-line bg-panel px-5
                   pb-[max(1.5rem,env(safe-area-inset-bottom))] pt-3"
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
          <ThumbsUp className="size-5" strokeWidth={sent === "up" ? 2.4 : 1.75} />
        </button>
        <button
          onClick={() => rate("down")}
          className={`flex flex-1 items-center justify-center rounded-full border py-3 active:scale-95 ${
            sent === "down" ? "border-danger bg-danger/15 text-danger" : "border-line text-fg"
          }`}
          aria-label="這張不行"
          aria-pressed={sent === "down"}
        >
          <ThumbsDown className="size-5" strokeWidth={sent === "down" ? 2.4 : 1.75} />
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
    <div className="fixed inset-0 z-20 flex items-end bg-ink/70" onClick={onClose}>
      <div
        onClick={(e) => e.stopPropagation()}
        className="w-full rounded-t-3xl border-t border-line bg-panel px-5
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
          <p className="mt-3 text-center text-xs text-muted">已收到你的回報，謝謝 🙏</p>
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
    <div className="fixed inset-0 z-30 flex items-end bg-ink/70" onClick={onClose}>
      <div
        onClick={(e) => e.stopPropagation()}
        className="max-h-[82dvh] w-full overflow-y-auto rounded-t-3xl border-t border-amber/40 bg-panel
                   px-5 pb-[max(1.5rem,env(safe-area-inset-bottom))] pt-3"
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
                <LeaderRow key={row.meme_id} row={row} place={i + 1} />
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

function LeaderRow({ row, place }: { row: LeaderboardEntry; place: number }) {
  const medal = MEDALS[place - 1];
  const top = place <= 3;
  return (
    <li
      className={`flex items-center gap-3 rounded-2xl border px-3 py-2.5 ${
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
