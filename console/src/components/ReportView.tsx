import { useEffect, useState } from "react";
import { fetchFeedbackReport } from "../lib/api";
import type { FeedbackReport, GroupRow } from "../types";

/** 回饋報表（docs/05 §2.2、docs/06 §3.6）：KPI、每日趨勢、分組通過率、👎 歸因。 */

function pct(rate: number | null): string {
  return rate === null ? "—" : `${Math.round(rate * 100)}%`;
}

function StatTile({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded border border-line bg-panel px-4 py-3">
      <p className="font-mono text-xs tracking-widest text-muted">{label}</p>
      <p className="mt-1 font-mono text-2xl font-semibold">{value}</p>
      {hint && <p className="mt-0.5 text-xs text-muted">{hint}</p>}
    </div>
  );
}

function Legend() {
  return (
    <div className="flex gap-4 font-mono text-xs text-muted">
      <span className="flex items-center gap-1.5">
        <span className="h-2.5 w-2.5 rounded-[2px] bg-chart-up" aria-hidden />
        👍 有用
      </span>
      <span className="flex items-center gap-1.5">
        <span className="h-2.5 w-2.5 rounded-[2px] bg-chart-down" aria-hidden />
        👎 不適合
      </span>
    </div>
  );
}

/** 每日 👍👎 分組長條（HTML/CSS 長條 + hover tooltip + 數據表備援）。 */
function DailyChart({ daily }: { daily: FeedbackReport["daily"] }) {
  const max = Math.max(1, ...daily.map((d) => Math.max(d.ups, d.downs)));
  return (
    <section className="rounded border border-line bg-panel p-4" aria-label="每日回饋趨勢">
      <div className="flex items-baseline justify-between">
        <h2 className="font-mono text-xs tracking-widest text-muted">每日回饋趨勢</h2>
        <Legend />
      </div>
      <div className="mt-3 overflow-x-auto">
        <div className="flex h-36 min-w-fit items-end gap-3 border-b border-line pb-px">
          {daily.map((d) => (
            <div key={d.date} className="group flex flex-col items-center gap-1">
              <div className="flex items-end gap-[2px]" style={{ height: "116px" }}>
                {[
                  { n: d.ups, cls: "bg-chart-up", label: "👍 有用" },
                  { n: d.downs, cls: "bg-chart-down", label: "👎 不適合" },
                ].map(({ n, cls, label }) => (
                  <div
                    key={label}
                    title={`${d.date}　${label} ${n} 筆`}
                    className={`w-3 rounded-t-[2px] ${cls} group-hover:opacity-100 ${
                      n === 0 ? "opacity-0" : "opacity-90"
                    }`}
                    style={{ height: `${Math.max((n / max) * 100, n > 0 ? 3 : 0)}%` }}
                  />
                ))}
              </div>
              <span className="font-mono text-[10px] text-muted">{d.date.slice(5)}</span>
            </div>
          ))}
        </div>
      </div>
      <details className="mt-2">
        <summary className="cursor-pointer font-mono text-xs text-muted hover:text-fg">
          數據表
        </summary>
        <table className="mt-2 text-sm">
          <thead className="text-left font-mono text-xs text-muted">
            <tr>
              <th className="pr-6">日期</th>
              <th className="pr-6 text-right">👍</th>
              <th className="text-right">👎</th>
            </tr>
          </thead>
          <tbody className="font-mono text-xs">
            {daily.map((d) => (
              <tr key={d.date}>
                <td className="pr-6 text-muted">{d.date}</td>
                <td className="pr-6 text-right">{d.ups}</td>
                <td className="text-right">{d.downs}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </section>
  );
}

/** 分組通過率：表格 + 單色率條（琥珀系）＋直接文字標籤，識別不靠顏色。 */
function GroupTable({
  title,
  rows,
  keyLabel,
  formatKey = (k) => String(k),
}: {
  title: string;
  rows: GroupRow[];
  keyLabel: string;
  formatKey?: (k: string | number) => string;
}) {
  return (
    <section className="rounded border border-line bg-panel p-4" aria-label={title}>
      <h2 className="font-mono text-xs tracking-widest text-muted">{title}</h2>
      {rows.length === 0 ? (
        <p className="mt-3 text-xs text-muted">尚無資料</p>
      ) : (
        <table className="mt-3 w-full text-sm">
          <thead className="text-left font-mono text-xs text-muted">
            <tr>
              <th className="py-1 pr-3 font-normal">{keyLabel}</th>
              <th className="py-1 pr-3 text-right font-normal">👍</th>
              <th className="py-1 pr-3 text-right font-normal">👎</th>
              <th className="w-1/3 py-1 font-normal">👍 率</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={String(row.key)} className="border-t border-line/50">
                <td className="max-w-48 truncate py-1.5 pr-3" title={formatKey(row.key)}>
                  {formatKey(row.key)}
                </td>
                <td className="py-1.5 pr-3 text-right font-mono text-xs">{row.ups}</td>
                <td className="py-1.5 pr-3 text-right font-mono text-xs">{row.downs}</td>
                <td className="py-1.5">
                  <div className="flex items-center gap-2">
                    <div
                      className="h-2 flex-1 rounded-[2px] bg-raised"
                      title={`${formatKey(row.key)}：👍 率 ${pct(row.up_rate)}（${
                        row.ups + row.downs
                      } 筆）`}
                    >
                      <div
                        className="h-2 rounded-[2px] bg-chart-rate"
                        style={{ width: `${(row.up_rate ?? 0) * 100}%` }}
                      />
                    </div>
                    <span className="w-9 text-right font-mono text-xs text-muted">
                      {pct(row.up_rate)}
                    </span>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

/** 👎 備註列表：供人工歸因到五類錯誤（docs/06 §3.6），對症調參。 */
function DownNotes({ notes }: { notes: FeedbackReport["down_notes"] }) {
  return (
    <section className="rounded border border-line bg-panel p-4" aria-label="👎 備註">
      <h2 className="font-mono text-xs tracking-widest text-muted">👎 備註（人工歸因用）</h2>
      <p className="mt-1.5 text-xs text-muted">
        逐條歸因到五類：<span className="text-fg">意圖錯</span>（intent 摘要就不對）／
        <span className="text-fg">query 爛</span>（策略 query 沒打中）／
        <span className="text-fg">庫缺圖</span>（庫裡根本沒有對的圖）／
        <span className="text-fg">排序錯</span>（對的圖在候選池卻沒進前排）／
        <span className="text-fg">梗過時</span>（圖對但梗已冷）——分別對症，避免只看總分瞎調。
      </p>
      {notes.length === 0 ? (
        <p className="mt-3 text-xs text-muted">尚無帶備註的 👎 回饋</p>
      ) : (
        <ul className="mt-3 space-y-2">
          {notes.map((note) => (
            <li
              key={`${note.query_id}-${note.meme_id}-${note.created_at}`}
              className="rounded border border-line/60 bg-raised/40 px-3 py-2 text-sm"
            >
              <p>「{note.note}」</p>
              <p className="mt-1 font-mono text-[11px] text-muted">
                {note.created_at.replace("T", " ").slice(5, 16)}　#{note.rank}
                {note.matched_strategy}
                {note.meme_ocr && `　圖：${note.meme_ocr.slice(0, 24)}`}
                {note.intent_summary && `　意圖：${note.intent_summary.slice(0, 40)}`}
              </p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

export default function ReportView() {
  const [report, setReport] = useState<FeedbackReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchFeedbackReport()
      .then(setReport)
      .catch((e) => setError(e instanceof Error ? e.message : "載入失敗"));
  }, []);

  if (error) return <p className="p-6 text-sm text-danger">{error}</p>;
  if (report === null) return <p className="p-6 text-sm text-muted">載入中…</p>;
  if (report.totals.total === 0)
    return (
      <p className="p-6 text-sm text-muted">
        尚無回饋資料——在工作台對推薦結果按 👍👎 後，這裡會累積出報表
      </p>
    );

  const { totals } = report;
  return (
    <div className="grid gap-4 overflow-y-auto p-4">
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatTile label="👍 率" value={pct(totals.up_rate)} hint="北極星指標" />
        <StatTile label="回饋總數" value={String(totals.total)} />
        <StatTile label="👍 / 👎" value={`${totals.ups} / ${totals.downs}`} />
        <StatTile label="有回饋的查詢" value={String(report.queries_with_feedback)} />
      </div>

      <DailyChart daily={report.daily} />

      <div className="grid gap-4 xl:grid-cols-2">
        <GroupTable title="依回應策略" rows={report.by_strategy} keyLabel="策略" />
        <GroupTable title="依系列（franchise）" rows={report.by_franchise} keyLabel="系列" />
        <GroupTable
          title="依推薦名次"
          rows={report.by_rank}
          keyLabel="名次"
          formatKey={(k) => `#${k}`}
        />
        <GroupTable title="依參數組合" rows={report.by_params} keyLabel="參數" />
      </div>

      <DownNotes notes={report.down_notes} />
    </div>
  );
}
