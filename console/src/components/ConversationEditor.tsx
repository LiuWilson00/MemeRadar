import { useState } from "react";
import { parseScreenshot } from "../lib/api";
import { EXAMPLES } from "../lib/examples";
import { parsePastedConversation } from "../lib/parseConversation";
import type { Turn } from "../types";

async function fileToBase64(file: File): Promise<string> {
  const dataUrl = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(new Error("讀取檔案失敗"));
    reader.readAsDataURL(file);
  });
  return dataUrl.slice(dataUrl.indexOf(",") + 1);
}

interface Props {
  turns: Turn[];
  onChange: (turns: Turn[]) => void;
  onSubmit: () => void;
  loading: boolean;
}

export default function ConversationEditor({ turns, onChange, onSubmit, loading }: Props) {
  const [pasteOpen, setPasteOpen] = useState(false);
  const [pasteText, setPasteText] = useState("");
  const [parsing, setParsing] = useState(false);
  const [parseNotice, setParseNotice] = useState<string[]>([]);

  const onScreenshot = async (file: File | undefined) => {
    if (!file || parsing) return;
    setParsing(true);
    setParseNotice([]);
    try {
      const parsed = await parseScreenshot(await fileToBase64(file));
      onChange(parsed.conversation.map((t) => ({ speaker: t.speaker, text: t.text })));
      setParseNotice([
        `已解析 ${parsed.conversation.length} 則訊息（判定來源：${parsed.app_guess}）——請確認左右方與內容後再送出`,
        ...parsed.warnings.map((w) => `⚠ ${w}`),
      ]);
    } catch (e) {
      setParseNotice([`✕ ${e instanceof Error ? e.message : "截圖解析失敗"}`]);
    } finally {
      setParsing(false);
    }
  };

  const update = (index: number, patch: Partial<Turn>) =>
    onChange(turns.map((t, i) => (i === index ? { ...t, ...patch } : t)));

  const canSubmit = !loading && turns.some((t) => t.text.trim());

  return (
    <section className="flex h-full flex-col gap-3" aria-label="對話輸入">
      <div className="flex items-center gap-2">
        <select
          className="min-w-0 flex-1 rounded border border-line bg-raised px-2 py-1.5 text-sm"
          value=""
          onChange={(e) => {
            const example = EXAMPLES[Number(e.target.value)];
            if (example) onChange(example.turns.map((t) => ({ ...t })));
          }}
        >
          <option value="" disabled>
            載入範例對話…
          </option>
          {EXAMPLES.map((ex, i) => (
            <option key={ex.label} value={i}>
              {ex.label}
            </option>
          ))}
        </select>
        <button
          className="rounded border border-line px-2 py-1.5 text-sm text-muted hover:text-fg"
          onClick={() => onChange([])}
        >
          清空
        </button>
      </div>

      <div className="flex-1 space-y-2 overflow-y-auto rounded border border-line bg-panel p-3">
        {turns.length === 0 && (
          <p className="py-8 text-center text-sm text-muted">
            貼上或輸入對話——最後一句應該是「你想用梗圖回覆的那句」
          </p>
        )}
        {turns.map((turn, i) => (
          <div key={i} className={`flex gap-2 ${turn.speaker === "me" ? "flex-row-reverse" : ""}`}>
            <button
              title="切換發話者"
              onClick={() => update(i, { speaker: turn.speaker === "me" ? "other" : "me" })}
              className={`h-7 shrink-0 self-center rounded-full px-2 font-mono text-xs ${
                turn.speaker === "me" ? "bg-amber-soft text-amber" : "bg-raised text-muted"
              }`}
            >
              {turn.speaker === "me" ? "我" : "對方"}
            </button>
            <input
              value={turn.text}
              placeholder="訊息內容"
              onChange={(e) => update(i, { text: e.target.value })}
              className={`min-w-0 flex-1 rounded-2xl border px-3 py-1.5 text-sm ${
                turn.speaker === "me"
                  ? "rounded-br-sm border-amber-soft bg-amber-soft"
                  : "rounded-bl-sm border-line bg-raised"
              }`}
            />
            <button
              aria-label="刪除這則"
              onClick={() => onChange(turns.filter((_, j) => j !== i))}
              className="self-center text-muted hover:text-danger"
            >
              ✕
            </button>
          </div>
        ))}
        <button
          className="w-full rounded border border-dashed border-line py-1.5 text-sm text-muted hover:text-fg"
          onClick={() => onChange([...turns, { speaker: "other", text: "" }])}
        >
          ＋ 新增訊息
        </button>
      </div>

      <div>
        <button className="text-xs text-muted underline" onClick={() => setPasteOpen(!pasteOpen)}>
          {pasteOpen ? "收合" : "貼上整段對話（自動拆句）"}
        </button>
        {pasteOpen && (
          <div className="mt-2 space-y-2">
            <textarea
              rows={4}
              value={pasteText}
              onChange={(e) => setPasteText(e.target.value)}
              placeholder={"支援「我：」「對方：」前綴；\n無前綴時自動交替（最後一句視為對方）"}
              className="w-full rounded border border-line bg-raised p-2 text-sm"
            />
            <button
              className="rounded border border-line px-3 py-1 text-sm hover:border-amber"
              onClick={() => {
                onChange(parsePastedConversation(pasteText));
                setPasteText("");
                setPasteOpen(false);
              }}
            >
              解析並取代
            </button>
          </div>
        )}
      </div>

      <div className="space-y-2">
        <button
          disabled={!canSubmit}
          onClick={onSubmit}
          className="w-full rounded bg-amber py-2.5 font-bold text-ink transition
                     hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {loading ? "掃描中…" : "推薦梗圖"}
        </button>
        <label
          className={`block rounded border border-dashed border-line py-2 text-center text-xs
                      ${parsing ? "cursor-wait text-amber" : "cursor-pointer text-muted hover:border-amber hover:text-fg"}`}
        >
          {parsing ? "解析截圖中…（約 5–8 秒）" : "上傳對話截圖（LINE / Messenger，PNG / JPEG / WebP）"}
          <input
            type="file"
            accept="image/png,image/jpeg,image/webp"
            className="hidden"
            disabled={parsing}
            onChange={(e) => {
              void onScreenshot(e.target.files?.[0]);
              e.target.value = "";
            }}
          />
        </label>
        {parseNotice.map((line) => (
          <p key={line} className="mt-1 text-xs text-muted">
            {line}
          </p>
        ))}
      </div>
    </section>
  );
}
