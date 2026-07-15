import { Bug, CheckCircle2, Loader2, Send, X } from "lucide-react";
import { useRef, useState } from "react";
import { sendBugReport } from "../lib/api";

/**
 * 浮動 bug 回報：貼右邊、半透明、可上下拖動的小鈕（絕不擋主內容）；
 * 點開填一句描述送出，連同最近操作麵包屑 + 裝置資訊一起回報後台。
 */

const POS_KEY = "memeradar.bugBtnY";

function initialY(): number {
  try {
    const saved = Number(localStorage.getItem(POS_KEY));
    if (Number.isFinite(saved) && saved > 0) return saved;
  } catch {
    /* ignore */
  }
  return Math.round((typeof window !== "undefined" ? window.innerHeight : 700) * 0.62);
}

export default function BugReporter() {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [y, setY] = useState(initialY);
  const drag = useRef<{ startY: number; origY: number; curY: number; moved: boolean } | null>(null);

  const onPointerDown = (e: React.PointerEvent) => {
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
    drag.current = { startY: e.clientY, origY: y, curY: y, moved: false };
  };
  const onPointerMove = (e: React.PointerEvent) => {
    const d = drag.current;
    if (!d) return;
    const dy = e.clientY - d.startY;
    if (Math.abs(dy) > 4) d.moved = true;
    d.curY = Math.min(window.innerHeight - 72, Math.max(56, d.origY + dy));
    setY(d.curY);
  };
  const onPointerUp = () => {
    const d = drag.current;
    drag.current = null;
    if (!d) return;
    if (d.moved) {
      try {
        localStorage.setItem(POS_KEY, String(d.curY));
      } catch {
        /* ignore */
      }
    } else {
      setOpen(true); // 純點擊 → 開啟回報
    }
  };

  const submit = async () => {
    const desc = text.trim();
    if (!desc || sending) return;
    setSending(true);
    setError(null);
    try {
      await sendBugReport(desc);
      setSent(true);
      setText("");
      setTimeout(() => {
        setOpen(false);
        setSent(false);
      }, 1600);
    } catch (e) {
      setError(e instanceof Error ? e.message : "回報失敗");
    } finally {
      setSending(false);
    }
  };

  return (
    <>
      <button
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        style={{ top: y }}
        aria-label="回報問題"
        title="回報問題"
        className="fixed right-1 z-40 grid size-9 touch-none place-items-center rounded-full
                   border border-line bg-panel/70 text-muted opacity-40 backdrop-blur
                   transition-opacity hover:opacity-90 active:opacity-100"
      >
        <Bug className="size-4" strokeWidth={2} />
      </button>

      {open && (
        <div
          className="fixed inset-0 z-50 flex flex-col justify-end"
          onClick={() => !sending && setOpen(false)}
        >
          <div className="absolute inset-0 bg-black/40 animate-fade-in" />
          <div
            onClick={(e) => e.stopPropagation()}
            className="relative rounded-t-2xl border-t border-line bg-panel p-4
                       pb-[max(1rem,env(safe-area-inset-bottom))] animate-sheet-up"
          >
            {sent ? (
              <div className="flex flex-col items-center gap-2 py-6 text-center">
                <CheckCircle2 className="size-9 text-signal" strokeWidth={1.8} />
                <p className="text-sm font-semibold">已回報，謝謝！</p>
                <p className="text-xs text-muted">我們會依你剛剛的操作紀錄排查</p>
              </div>
            ) : (
              <>
                <div className="mb-2 flex items-center gap-2">
                  <Bug className="size-4 text-amber" />
                  <p className="text-sm font-semibold">回報問題</p>
                  <button
                    onClick={() => setOpen(false)}
                    className="ml-auto text-muted active:text-fg"
                    aria-label="關閉"
                  >
                    <X className="size-5" />
                  </button>
                </div>
                <textarea
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  rows={3}
                  maxLength={2000}
                  // eslint-disable-next-line jsx-a11y/no-autofocus
                  autoFocus
                  placeholder="遇到什麼問題？例如：上傳截圖後一直轉圈…"
                  className="w-full resize-none rounded-xl border border-line bg-ink px-3 py-2.5
                             text-sm outline-none focus:border-amber"
                />
                <p className="mt-1.5 text-[11px] text-muted">
                  會一併附上你最近的操作紀錄與裝置資訊，方便重現。不含對話內容。
                </p>
                {error && <p className="mt-1 text-xs text-danger">{error}</p>}
                <button
                  onClick={submit}
                  disabled={!text.trim() || sending}
                  className="mt-3 flex w-full items-center justify-center gap-1.5 rounded-full
                             bg-amber py-3 text-sm font-semibold text-ink disabled:opacity-40 active:opacity-80"
                >
                  {sending ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}
                  送出回報
                </button>
              </>
            )}
          </div>
        </div>
      )}
    </>
  );
}
