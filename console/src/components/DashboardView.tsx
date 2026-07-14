import { RefreshCw } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { fetchDashboard } from "../lib/api";
import type { Dashboard } from "../types";

/** 全站監控儀表板：使用量 / 推薦延遲 / NVIDIA 用量 / 標註速度 / 回饋 / 圖庫。 */

function fmtMs(ms: number | null | undefined): string {
  if (ms == null) return "—";
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`;
}
function pct(r: number | null): string {
  return r == null ? "—" : `${Math.round(r * 100)}%`;
}

function Tile({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded border border-line bg-panel px-4 py-3">
      <p className="font-mono text-xs tracking-widest text-muted">{label}</p>
      <p className="mt-1 font-mono text-2xl font-semibold">{value}</p>
      {hint && <p className="mt-0.5 text-xs text-muted">{hint}</p>}
    </div>
  );
}

/** 水平長條列（每日推薦 / 圖庫分布）。 */
function BarList({
  title,
  rows,
  accent = "bg-amber",
}: {
  title: string;
  rows: { name: string; count: number }[];
  accent?: string;
}) {
  const max = Math.max(1, ...rows.map((r) => r.count));
  return (
    <section className="rounded border border-line bg-panel p-4">
      <h3 className="mb-3 text-sm font-semibold">{title}</h3>
      {rows.length === 0 ? (
        <p className="text-xs text-muted">尚無資料</p>
      ) : (
        <div className="grid gap-1.5">
          {rows.map((r) => (
            <div key={r.name} className="flex items-center gap-2 text-xs">
              <span className="w-28 shrink-0 truncate text-muted" title={r.name}>
                {r.name}
              </span>
              <div className="h-4 flex-1 overflow-hidden rounded-[3px] bg-raised">
                <div
                  className={`h-full rounded-[3px] ${accent}`}
                  style={{ width: `${(r.count / max) * 100}%` }}
                />
              </div>
              <span className="w-10 shrink-0 text-right font-mono">{r.count}</span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

const STAGES: { key: string; label: string }[] = [
  { key: "intent", label: "意圖" },
  { key: "retrieval", label: "檢索" },
  { key: "rerank", label: "重排序" },
  { key: "total", label: "總計" },
];

export default function DashboardView() {
  const [data, setData] = useState<Dashboard | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    setError(null);
    fetchDashboard()
      .then(setData)
      .catch((e) => setError(e instanceof Error ? e.message : "載入失敗"));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  if (error) return <p className="p-6 text-sm text-danger">{error}</p>;
  if (data === null) return <p className="p-6 text-sm text-muted">載入中…</p>;

  const ov = data.overview;
  const lat = data.latency_ms;

  return (
    <div className="grid gap-4 overflow-y-auto p-4">
      <div className="flex items-center gap-3">
        <h2 className="text-sm font-semibold">監控儀表板</h2>
        <button
          onClick={load}
          className="ml-auto flex items-center gap-1 rounded border border-line px-2.5 py-1 text-xs text-muted hover:text-fg"
        >
          <RefreshCw className="size-3" /> 重新整理
        </button>
      </div>

      {/* 使用量 */}
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
        <Tile label="推薦總數" value={String(ov.recommendations_total)} hint={`近 7 天 ${ov.recommendations_7d}`} />
        <Tile label="不重複用戶" value={String(ov.unique_clients)} hint="依 client id" />
        <Tile label="任務總數" value={String(ov.tasks_total)} />
        <Tile label="👍 率" value={pct(ov.feedback_up_rate)} hint={`${ov.feedback_ups} / ${ov.feedback_ups + ov.feedback_downs}`} />
        <Tile label="啟用中梗圖" value={String(ov.memes_active)} hint={`共 ${ov.memes_total}`} />
        <Tile label="向量覆蓋" value={pct(ov.embedding_coverage)} hint={`${ov.embeddings} 個向量`} />
      </div>

      {/* 每日推薦 */}
      <BarList
        title="每日推薦（近 14 天）"
        rows={data.daily_recommendations.map((d) => ({ name: d.date.slice(5), count: d.count }))}
      />

      <div className="grid gap-4 xl:grid-cols-2">
        {/* 推薦延遲 */}
        <section className="rounded border border-line bg-panel p-4">
          <h3 className="mb-3 text-sm font-semibold">推薦延遲（各階段 p50 / p95）</h3>
          <table className="w-full text-left font-mono text-xs">
            <thead className="text-muted">
              <tr className="border-b border-line">
                <th className="py-1.5 pr-4 font-normal">階段</th>
                <th className="py-1.5 pr-4 text-right font-normal">p50</th>
                <th className="py-1.5 text-right font-normal">p95</th>
              </tr>
            </thead>
            <tbody>
              {STAGES.map((s) => (
                <tr key={s.key} className="border-b border-line/50">
                  <td className="py-1.5 pr-4">{s.label}</td>
                  <td className="py-1.5 pr-4 text-right">{fmtMs(lat[`${s.key}_p50`])}</td>
                  <td className="py-1.5 text-right">{fmtMs(lat[`${s.key}_p95`])}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-2 text-xs text-muted">
            意圖/重排序走 NVIDIA 免費層，延遲較高屬正常；檢索為 pgvector，通常數百 ms。
          </p>
        </section>

        {/* NVIDIA 用量 / 標註速度 */}
        <section className="rounded border border-line bg-panel p-4">
          <h3 className="mb-3 text-sm font-semibold">NVIDIA 呼叫用量（各任務 × 狀態）</h3>
          {data.vlm_calls.length === 0 ? (
            <p className="text-xs text-muted">尚無呼叫紀錄</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left font-mono text-xs">
                <thead className="text-muted">
                  <tr className="border-b border-line">
                    <th className="py-1.5 pr-4 font-normal">任務</th>
                    <th className="py-1.5 pr-4 font-normal">狀態</th>
                    <th className="py-1.5 pr-4 text-right font-normal">次數</th>
                    <th className="py-1.5 text-right font-normal">平均延遲</th>
                  </tr>
                </thead>
                <tbody>
                  {data.vlm_calls.map((r, i) => (
                    <tr key={i} className="border-b border-line/50">
                      <td className="py-1.5 pr-4">{r.task}</td>
                      <td
                        className={`py-1.5 pr-4 ${
                          r.status === "ok"
                            ? "text-signal"
                            : r.status === "rate_limited"
                              ? "text-amber"
                              : "text-danger"
                        }`}
                      >
                        {r.status}
                      </td>
                      <td className="py-1.5 pr-4 text-right">{r.count}</td>
                      <td className="py-1.5 text-right">{fmtMs(r.avg_ms)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>

      {/* 圖庫分布 */}
      <div className="grid gap-4 xl:grid-cols-2">
        <BarList title="梗圖庫 · 依系列（Top 8）" rows={data.library.by_franchise} accent="bg-chart-up" />
        <BarList title="梗圖庫 · 依分類（Top 8）" rows={data.library.by_category} accent="bg-amber/70" />
      </div>
    </div>
  );
}
