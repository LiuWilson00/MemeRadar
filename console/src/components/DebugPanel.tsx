import type { RecommendResponse } from "../types";

/** 開發者向：意圖 JSON、檢索 query 與回收數、候選池表格、耗時（docs/05 §2.1） */
export default function DebugPanel({ response }: { response: RecommendResponse }) {
  const { intent, debug, query_id } = response;
  const totalMs = debug.timings_ms.total ?? 1;
  const stages = Object.entries(debug.timings_ms).filter(([k]) => k !== "total");
  const strategyNames = intent.strategies.map((s) => s.name);

  return (
    <details className="rounded-lg border border-line bg-panel">
      <summary className="cursor-pointer select-none px-4 py-2.5 font-mono text-xs tracking-widest text-muted hover:text-fg">
        DEBUG ▾ <span className="ml-2 normal-case">query_id={query_id}</span>
        <button
          className="ml-2 rounded border border-line px-1.5 text-[10px] hover:text-amber"
          onClick={(e) => {
            e.preventDefault();
            navigator.clipboard?.writeText(query_id);
          }}
        >
          複製
        </button>
        {debug.rerank_fallback && (
          <span className="ml-3 rounded bg-danger/20 px-1.5 py-0.5 text-danger">
            rerank 失效——已退回向量排序
          </span>
        )}
      </summary>

      <div className="grid gap-4 border-t border-line p-4 text-xs lg:grid-cols-2">
        <section>
          <h4 className="mb-1.5 font-mono tracking-widest text-muted">意圖分析</h4>
          <div className="space-y-1 rounded bg-ink p-3">
            <p>
              <span className="text-muted">摘要：</span>
              {intent.summary}
            </p>
            <p>
              <span className="text-muted">爆點句：</span>「{intent.punchline}」
            </p>
            <p>
              <span className="text-muted">對方情緒：</span>
              {intent.other_party_emotion.join("、")}
              <span className="ml-3 text-muted">類型：</span>
              {intent.conversation_type}
            </p>
            {intent.sensitive && <p className="text-danger">⚠ 敏感情境——策略已降級為僅安撫</p>}
            {intent.low_context && <p className="text-amber">△ 上下文不足，採泛用策略</p>}
          </div>
        </section>

        <section>
          <h4 className="mb-1.5 font-mono tracking-widest text-muted">檢索（各策略 query 與回收數）</h4>
          <table className="w-full">
            <tbody>
              {intent.strategies.map((s) => (
                <tr key={s.name} className="border-b border-line/50">
                  <td className="py-1 pr-2 whitespace-nowrap text-amber">{s.name}</td>
                  <td className="py-1 pr-2 text-muted">「{s.query}」</td>
                  <td className="py-1 text-right font-mono">
                    {debug.per_strategy_hits[s.name] ?? 0}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        <section className="lg:col-span-2">
          <h4 className="mb-1.5 font-mono tracking-widest text-muted">
            候選池（{debug.candidates.length} 張，合併後依向量分排序）
          </h4>
          <div className="max-h-56 overflow-auto rounded bg-ink">
            <table className="w-full font-mono">
              <thead className="sticky top-0 bg-ink text-muted">
                <tr>
                  <th className="px-2 py-1 text-left">圖中文字</th>
                  <th className="px-2 py-1 text-right">vector</th>
                  {strategyNames.map((n) => (
                    <th key={n} className="px-2 py-1 text-right font-sans">
                      {n}
                    </th>
                  ))}
                  <th className="px-2 py-1">入選</th>
                </tr>
              </thead>
              <tbody>
                {debug.candidates.map((c) => (
                  <tr key={c.meme_id} className={c.in_top ? "" : "text-muted"}>
                    <td className="px-2 py-1 font-sans">{c.ocr_text || "（無）"}</td>
                    <td className="px-2 py-1 text-right">{c.vector.toFixed(3)}</td>
                    {strategyNames.map((n) => (
                      <td key={n} className="px-2 py-1 text-right">
                        {c.per_strategy[n]?.toFixed(3) ?? "—"}
                      </td>
                    ))}
                    <td className="px-2 py-1 text-center">{c.in_top ? "✓" : ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section className="lg:col-span-2">
          <h4 className="mb-1.5 font-mono tracking-widest text-muted">耗時</h4>
          <div className="space-y-1">
            {stages.map(([name, ms]) => (
              <div key={name} className="flex items-center gap-2">
                <span className="w-16 font-mono text-muted">{name}</span>
                <div className="h-1.5 flex-1 overflow-hidden rounded bg-ink">
                  <div className="h-full bg-amber/70" style={{ width: `${(ms / totalMs) * 100}%` }} />
                </div>
                <span className="w-16 text-right font-mono">{ms}ms</span>
              </div>
            ))}
            <p className="text-right font-mono text-muted">total {totalMs}ms</p>
          </div>
        </section>
      </div>
    </details>
  );
}
