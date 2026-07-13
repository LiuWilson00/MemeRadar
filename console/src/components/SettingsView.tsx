import { Check, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { fetchModelSettings, fetchVlmUsage, updateModelSettings } from "../lib/api";
import type { ModelSettings, VlmUsageRow } from "../types";

/** 後台設定：各任務（標註/意圖/rerank/截圖/對方梗圖）模型可調 + NVIDIA 用量檢視。 */
export default function SettingsView() {
  const [settings, setSettings] = useState<ModelSettings | null>(null);
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    fetchModelSettings()
      .then((s) => {
        setSettings(s);
        setDraft(Object.fromEntries(s.tasks.map((t) => [t.key, t.current ?? ""])));
      })
      .catch((e) => setError(e instanceof Error ? e.message : "載入失敗"));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const dirty = useMemo(
    () => settings?.tasks.some((t) => draft[t.key] !== (t.current ?? "")) ?? false,
    [settings, draft],
  );

  const save = async () => {
    setSaving(true);
    setSaved(false);
    setError(null);
    try {
      // 空字串 → null（回預設）
      const models = Object.fromEntries(
        Object.entries(draft).map(([k, v]) => [k, v || null]),
      );
      await updateModelSettings(models);
      load();
      setSaved(true);
      setTimeout(() => setSaved(false), 2000);
    } catch (e) {
      setError(e instanceof Error ? e.message : "儲存失敗");
    } finally {
      setSaving(false);
    }
  };

  if (error && settings === null) return <p className="p-6 text-sm text-danger">{error}</p>;
  if (settings === null) return <p className="p-6 text-sm text-muted">載入中…</p>;

  return (
    <div className="grid gap-4 overflow-y-auto p-4">
      <section className="rounded border border-line bg-panel p-4">
        <h2 className="text-sm font-semibold">各任務模型</h2>
        <p className="mt-1 text-xs text-muted">
          設定各步驟要用的 NVIDIA 模型；選「預設」則沿用伺服器啟動時的模型（
          <span className="font-mono text-amber">{settings.default ?? "—"}</span>）。變更即時生效。
        </p>

        <div className="mt-4 grid gap-2">
          {settings.tasks.map((t) => (
            <label
              key={t.key}
              className="grid grid-cols-[130px_1fr] items-center gap-3 rounded border border-line bg-raised px-3 py-2"
            >
              <span className="text-sm">{t.label}</span>
              <select
                value={draft[t.key] ?? ""}
                onChange={(e) => setDraft((d) => ({ ...d, [t.key]: e.target.value }))}
                className="w-full rounded border border-line bg-panel px-2 py-1.5 font-mono text-xs outline-none focus:border-amber"
              >
                <option value="">預設（{settings.default ?? "—"}）</option>
                {settings.available.map((m) => (
                  <option key={m} value={m}>
                    {m}
                  </option>
                ))}
              </select>
            </label>
          ))}
        </div>

        <div className="mt-4 flex items-center gap-3">
          <button
            onClick={save}
            disabled={!dirty || saving}
            className="rounded bg-amber px-4 py-1.5 text-xs font-semibold text-ink disabled:opacity-40"
          >
            {saving ? "儲存中…" : "儲存變更"}
          </button>
          {saved && (
            <span className="flex items-center gap-1 text-xs text-signal">
              <Check className="size-3.5" /> 已套用
            </span>
          )}
          {error && <span className="text-xs text-danger">{error}</span>}
        </div>
      </section>

      <UsagePanel />
    </div>
  );
}

/** NVIDIA 呼叫用量：各 key × 狀態的呼叫數與平均延遲（監控哪把 key 被限流）。 */
function UsagePanel() {
  const [usage, setUsage] = useState<VlmUsageRow[] | null>(null);

  const load = useCallback(() => {
    fetchVlmUsage()
      .then(setUsage)
      .catch(() => setUsage([]));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const statusLabel: Record<string, string> = {
    ok: "成功",
    rate_limited: "限流",
    error: "錯誤",
    parse_fail: "解析失敗",
  };

  return (
    <section className="rounded border border-line bg-panel p-4">
      <div className="flex items-center gap-3">
        <h2 className="text-sm font-semibold">NVIDIA 呼叫用量</h2>
        <button
          onClick={load}
          className="ml-auto flex items-center gap-1 rounded border border-line px-2.5 py-1 text-xs text-muted hover:text-fg"
        >
          <RefreshCw className="size-3" /> 重新整理
        </button>
      </div>

      {usage === null ? (
        <p className="mt-3 text-xs text-muted">載入中…</p>
      ) : usage.length === 0 ? (
        <p className="mt-3 text-xs text-muted">尚無呼叫紀錄——跑幾筆推薦或上傳標註後這裡會累積。</p>
      ) : (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-left font-mono text-xs">
            <thead className="text-muted">
              <tr className="border-b border-line">
                <th className="py-1.5 pr-4 font-normal">Key</th>
                <th className="py-1.5 pr-4 font-normal">狀態</th>
                <th className="py-1.5 pr-4 text-right font-normal">呼叫數</th>
                <th className="py-1.5 text-right font-normal">平均延遲</th>
              </tr>
            </thead>
            <tbody>
              {usage.map((r, i) => (
                <tr key={i} className="border-b border-line/50">
                  <td className="py-1.5 pr-4">{r.key_id ?? "—"}</td>
                  <td
                    className={`py-1.5 pr-4 ${
                      r.status === "ok"
                        ? "text-signal"
                        : r.status === "rate_limited"
                          ? "text-amber"
                          : "text-danger"
                    }`}
                  >
                    {statusLabel[r.status] ?? r.status}
                  </td>
                  <td className="py-1.5 pr-4 text-right">{r.n}</td>
                  <td className="py-1.5 text-right">
                    {r.avg_ms === null ? "—" : `${Math.round(r.avg_ms)} ms`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
