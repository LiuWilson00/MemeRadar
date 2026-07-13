import { AlertTriangle, LogOut } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import AdminGate, { logout } from "./components/AdminGate";
import ConversationEditor from "./components/ConversationEditor";
import DebugPanel from "./components/DebugPanel";
import HistoryView from "./components/HistoryView";
import LibraryView from "./components/LibraryView";
import ParamsPanel from "./components/ParamsPanel";
import RadarLoading from "./components/RadarLoading";
import ReportView from "./components/ReportView";
import ResultCard from "./components/ResultCard";
import ReviewView from "./components/ReviewView";
import SettingsView from "./components/SettingsView";
import UploadView from "./components/UploadView";
import { DEFAULT_FILTERS, DEFAULT_PARAMS, fetchMeta, recommend } from "./lib/api";
import type { Filters, HistoryDetail, Meta, Params, RecommendResponse, Turn } from "./types";

type Tab = "work" | "history" | "library" | "upload" | "review" | "report" | "settings";
const TABS: Array<{ id: Tab; label: string }> = [
  { id: "work", label: "工作台" },
  { id: "history", label: "查詢歷史" },
  { id: "library", label: "梗圖庫" },
  { id: "upload", label: "上傳" },
  { id: "review", label: "複核" },
  { id: "report", label: "報表" },
  { id: "settings", label: "設定" },
];

export default function App() {
  const [tab, setTab] = useState<Tab>("work");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [filters, setFilters] = useState<Filters>({ ...DEFAULT_FILTERS });
  const [params, setParams] = useState<Params>({ ...DEFAULT_PARAMS });
  const [meta, setMeta] = useState<Meta | null>(null);
  const [apiUp, setApiUp] = useState<boolean | null>(null);
  const [loading, setLoading] = useState(false);
  const [response, setResponse] = useState<RecommendResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/health")
      .then((r) => setApiUp(r.ok))
      .catch(() => setApiUp(false));
    fetchMeta()
      .then(setMeta)
      .catch(() => setMeta(null));
  }, []);

  const runQuery = useCallback(
    async (queryTurns: Turn[], queryFilters: Filters, queryParams: Params) => {
      const cleaned = queryTurns.filter((t) => t.text.trim());
      if (cleaned.length === 0) return;
      setLoading(true);
      setError(null);
      try {
        setResponse(await recommend(cleaned, queryFilters, queryParams));
      } catch (e) {
        setResponse(null);
        setError(e instanceof Error ? e.message : "查詢失敗");
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  const submit = useCallback(() => {
    if (!loading) void runQuery(turns, filters, params);
  }, [turns, filters, params, loading, runQuery]);

  /** 歷史重放：載入當時輸入與參數回工作台並自動重跑（docs/05 §2.2） */
  const replay = useCallback(
    (detail: HistoryDetail) => {
      const replayTurns = detail.conversation;
      const replayFilters = { ...DEFAULT_FILTERS, ...detail.params_snapshot.filters };
      const replayParams = { ...DEFAULT_PARAMS, ...detail.params_snapshot.params };
      setTurns(replayTurns);
      setFilters(replayFilters);
      setParams(replayParams);
      setTab("work");
      void runQuery(replayTurns, replayFilters, replayParams);
    },
    [runQuery],
  );

  const hits = response?.debug.per_strategy_hits ?? {};
  const allZeroHits = Object.values(hits).length > 0 && Object.values(hits).every((n) => n === 0);

  return (
    <AdminGate>
    <div className="flex h-screen flex-col">
      <header className="flex items-center gap-3 border-b border-line px-4 py-2.5">
        <h1 className="font-mono text-sm font-semibold tracking-[0.3em]">
          MEME<span className="text-amber">RADAR</span>
        </h1>
        <span className="text-xs text-muted">調適控制台</span>
        <nav className="ml-6 flex gap-1">
          {TABS.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`rounded px-3 py-1 text-xs ${
                tab === t.id ? "bg-amber-soft text-amber" : "text-muted hover:text-fg"
              }`}
            >
              {t.label}
            </button>
          ))}
        </nav>
        <span className="ml-auto flex items-center gap-1.5 font-mono text-xs text-muted">
          <span
            className={`h-2 w-2 rounded-full ${
              apiUp === null ? "bg-line" : apiUp ? "bg-signal" : "bg-danger"
            }`}
          />
          API {apiUp === false ? "離線——請啟動 python -m memeradar.api" : apiUp ? "連線中" : "…"}
        </span>
        <button
          onClick={logout}
          title="登出"
          className="flex items-center gap-1 text-xs text-muted hover:text-fg"
        >
          <LogOut className="size-3.5" /> 登出
        </button>
      </header>

      {tab === "history" && <HistoryView onReplay={replay} />}
      {tab === "library" && <LibraryView meta={meta} />}
      {tab === "upload" && (
        <UploadView onDone={() => fetchMeta().then(setMeta).catch(() => {})} />
      )}
      {tab === "review" && <ReviewView meta={meta} />}
      {tab === "report" && <ReportView />}
      {tab === "settings" && <SettingsView />}

      <main
        className={`min-h-0 flex-1 grid-cols-[300px_1fr_260px] gap-4 p-4 ${
          tab === "work" ? "grid" : "hidden"
        }`}
      >
        <ConversationEditor turns={turns} onChange={setTurns} onSubmit={submit} loading={loading} />

        <section className="flex min-h-0 flex-col gap-3 overflow-y-auto" aria-label="推薦結果">
          {loading && <RadarLoading />}

          {!loading && error && (
            <div className="rounded border border-danger/50 bg-danger/10 p-4 text-sm">
              <p className="font-semibold text-danger">查詢失敗</p>
              <p className="mt-1 text-muted">{error}</p>
            </div>
          )}

          {!loading && !error && !response && (
            <div className="flex flex-1 flex-col items-center justify-center gap-3 text-center">
              <div className="radar h-32 w-32 opacity-40" />
              <p className="text-sm text-muted">
                左側輸入對話（或載入範例）後按「推薦梗圖」開始掃描
              </p>
            </div>
          )}

          {!loading && response && (
            <>
              {response.intent.sensitive && (
                <p className="flex items-center gap-1.5 rounded border border-amber/50 bg-amber-soft px-3 py-2 text-xs">
                  <AlertTriangle className="size-3.5 shrink-0 text-amber" />
                  偵測到敏感情境——回應策略已降級為僅「安撫」
                </p>
              )}
              {response.results.length === 0 ? (
                <div className="rounded border border-line bg-panel p-5 text-sm">
                  <p className="font-semibold">沒有找到適合的梗圖</p>
                  <p className="mt-1 text-muted">
                    {allZeroHits
                      ? "所有策略皆無命中：可能是庫太小、過濾條件太嚴，或 min_similarity 過高——試著放寬右側條件後重跑。"
                      : "候選在重排序階段被全數淘汰，可於 Debug 面板檢視候選池。"}
                  </p>
                </div>
              ) : (
                <div className="grid grid-cols-1 gap-3 xl:grid-cols-2 2xl:grid-cols-3">
                  {response.results.map((item) => (
                    <ResultCard key={item.meme_id} item={item} queryId={response.query_id} />
                  ))}
                </div>
              )}
              <DebugPanel response={response} />
            </>
          )}
        </section>

        <ParamsPanel
          meta={meta}
          filters={filters}
          params={params}
          onFilters={setFilters}
          onParams={setParams}
          onRerun={submit}
          canRerun={!loading && turns.some((t) => t.text.trim())}
        />
      </main>
    </div>
    </AdminGate>
  );
}
