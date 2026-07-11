import { useEffect, useState } from "react";
import { fetchHistory, fetchHistoryDetail } from "../lib/api";
import type { HistoryDetail, HistoryItem } from "../types";

interface Props {
  onReplay: (detail: HistoryDetail) => void;
}

/** 查詢歷史（docs/05 §2.2）：時間、輸入摘要、參數、👍👎 統計、重放。 */
export default function HistoryView({ onReplay }: Props) {
  const [items, setItems] = useState<HistoryItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [replaying, setReplaying] = useState<string | null>(null);

  useEffect(() => {
    fetchHistory()
      .then(setItems)
      .catch((e) => setError(e instanceof Error ? e.message : "載入失敗"));
  }, []);

  const replay = async (queryId: string) => {
    setReplaying(queryId);
    try {
      onReplay(await fetchHistoryDetail(queryId));
    } catch (e) {
      setError(e instanceof Error ? e.message : "重放載入失敗");
      setReplaying(null);
    }
  };

  if (error) return <p className="p-6 text-sm text-danger">{error}</p>;
  if (items === null) return <p className="p-6 text-sm text-muted">載入中…</p>;
  if (items.length === 0)
    return <p className="p-6 text-sm text-muted">尚無查詢紀錄——回到工作台跑第一次推薦吧</p>;

  return (
    <div className="overflow-auto p-4">
      <table className="w-full text-sm">
        <thead className="text-left font-mono text-xs tracking-widest text-muted">
          <tr className="border-b border-line">
            <th className="px-2 py-2">時間</th>
            <th className="px-2 py-2">輸入摘要</th>
            <th className="px-2 py-2">參數</th>
            <th className="px-2 py-2 text-right">結果</th>
            <th className="px-2 py-2 text-right">👍</th>
            <th className="px-2 py-2 text-right">👎</th>
            <th className="px-2 py-2 text-right">耗時</th>
            <th className="px-2 py-2"></th>
          </tr>
        </thead>
        <tbody>
          {items.map((item) => {
            const lastOther = [...item.conversation].reverse().find((t) => t.speaker === "other");
            const params = item.params_snapshot.params;
            return (
              <tr key={item.query_id} className="border-b border-line/50 hover:bg-panel">
                <td className="whitespace-nowrap px-2 py-2 font-mono text-xs text-muted">
                  {item.created_at.replace("T", " ").slice(5, 16)}
                </td>
                <td className="max-w-72 truncate px-2 py-2" title={lastOther?.text}>
                  {lastOther?.text ?? "（截圖輸入）"}
                </td>
                <td className="px-2 py-2 font-mono text-xs text-muted">
                  {params
                    ? `n=${params.top_n} sim≥${params.min_similarity} div=${params.diversity}`
                    : "—"}
                </td>
                <td className="px-2 py-2 text-right font-mono">{item.result_count}</td>
                <td className="px-2 py-2 text-right font-mono text-signal">{item.ups || ""}</td>
                <td className="px-2 py-2 text-right font-mono text-danger">{item.downs || ""}</td>
                <td className="px-2 py-2 text-right font-mono text-xs text-muted">
                  {item.latency_ms != null ? `${(item.latency_ms / 1000).toFixed(1)}s` : "—"}
                </td>
                <td className="px-2 py-2 text-right">
                  <button
                    disabled={replaying !== null}
                    onClick={() => replay(item.query_id)}
                    className="rounded border border-line px-2 py-0.5 text-xs text-muted
                               hover:border-amber hover:text-amber disabled:opacity-40"
                  >
                    {replaying === item.query_id ? "載入…" : "重放"}
                  </button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
