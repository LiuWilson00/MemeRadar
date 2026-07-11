import { useCallback, useEffect, useState } from "react";
import ConversationEditor from "./components/ConversationEditor";
import DebugPanel from "./components/DebugPanel";
import ParamsPanel from "./components/ParamsPanel";
import RadarLoading from "./components/RadarLoading";
import ResultCard from "./components/ResultCard";
import { DEFAULT_FILTERS, DEFAULT_PARAMS, fetchMeta, recommend } from "./lib/api";
import type { Filters, Meta, Params, RecommendResponse, Turn } from "./types";

export default function App() {
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

  const submit = useCallback(async () => {
    const cleaned = turns.filter((t) => t.text.trim());
    if (cleaned.length === 0 || loading) return;
    setLoading(true);
    setError(null);
    try {
      setResponse(await recommend(cleaned, filters, params));
    } catch (e) {
      setResponse(null);
      setError(e instanceof Error ? e.message : "查詢失敗");
    } finally {
      setLoading(false);
    }
  }, [turns, filters, params, loading]);

  const hits = response?.debug.per_strategy_hits ?? {};
  const allZeroHits = Object.values(hits).length > 0 && Object.values(hits).every((n) => n === 0);

  return (
    <div className="flex h-screen flex-col">
      <header className="flex items-center gap-3 border-b border-line px-4 py-2.5">
        <h1 className="font-mono text-sm font-semibold tracking-[0.3em]">
          MEME<span className="text-amber">RADAR</span>
        </h1>
        <span className="text-xs text-muted">調適控制台</span>
        <span className="ml-auto flex items-center gap-1.5 font-mono text-xs text-muted">
          <span
            className={`h-2 w-2 rounded-full ${
              apiUp === null ? "bg-line" : apiUp ? "bg-signal" : "bg-danger"
            }`}
          />
          API {apiUp === false ? "離線——請啟動 python -m memeradar.api" : apiUp ? "連線中" : "…"}
        </span>
      </header>

      <main className="grid min-h-0 flex-1 grid-cols-[300px_1fr_260px] gap-4 p-4">
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
                <p className="rounded border border-amber/50 bg-amber-soft px-3 py-2 text-xs">
                  ⚠ 偵測到敏感情境——回應策略已降級為僅「安撫」
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
  );
}
