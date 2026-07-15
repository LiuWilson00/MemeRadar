import { Loader2, MessageCircle, Send, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import MemeImage from "../components/MemeImage";
import { chat } from "../lib/api";
import type { ChatMeme } from "../types";

/** 只會回梗圖的朋友：你打字，他每則都丟一張梗圖回你（embedding 檢索、秒回）。 */

type ChatMsg =
  | { role: "user"; id: string; text: string }
  | { role: "bot"; id: string; meme: ChatMeme | null };

const STORAGE_KEY = "memeradar.chatHistory";
const MAX_KEEP = 100;

function uid(): string {
  return Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
}

function load(): ChatMsg[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as ChatMsg[]) : [];
  } catch {
    return [];
  }
}

export default function ChatScreen() {
  const [messages, setMessages] = useState<ChatMsg[]>(load);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(messages.slice(-MAX_KEEP)));
    } catch {
      /* 空間不足 → 略過 */
    }
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, sending]);

  const send = async () => {
    const text = input.trim();
    if (!text || sending) return;
    setInput("");
    // 這輪已回過的梗圖 → 避免一直重複（帶最近 30 張）
    const exclude = messages
      .filter((m): m is Extract<ChatMsg, { role: "bot" }> => m.role === "bot" && m.meme !== null)
      .slice(-30)
      .map((m) => m.meme!.meme_id);
    setMessages((m) => [...m, { role: "user", id: uid(), text }]);
    setSending(true);
    try {
      const reply = await chat(text, exclude);
      setMessages((m) => [...m, { role: "bot", id: uid(), meme: reply.meme }]);
    } catch {
      setMessages((m) => [...m, { role: "bot", id: uid(), meme: null }]);
    } finally {
      setSending(false);
    }
  };

  const clear = () => {
    setMessages([]);
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch {
      /* ignore */
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex-1 space-y-3 overflow-y-auto px-4 py-4">
        {messages.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
            <div className="grid size-16 place-items-center rounded-full bg-amber-soft">
              <MessageCircle className="size-8 text-amber" strokeWidth={1.75} />
            </div>
            <p className="text-base font-semibold">只會回梗圖的朋友</p>
            <p className="max-w-[16rem] text-sm leading-relaxed text-muted">
              跟他聊聊，你說什麼他都<span className="text-amber">丟梗圖</span>回你 😌
            </p>
          </div>
        ) : (
          messages.map((m) =>
            m.role === "user" ? (
              <div key={m.id} className="flex justify-end animate-fade-in-up">
                <div className="max-w-[75%] rounded-2xl rounded-br-md bg-amber px-3.5 py-2 text-sm text-ink">
                  {m.text}
                </div>
              </div>
            ) : (
              <div key={m.id} className="flex justify-start animate-fade-in-up">
                {m.meme ? (
                  <div className="max-w-[75%] overflow-hidden rounded-2xl rounded-bl-md border border-line bg-panel">
                    <MemeImage
                      src={m.meme.image_url}
                      alt={m.meme.ocr_text ?? "梗圖"}
                      className="max-h-[42vh] w-full object-contain"
                    />
                  </div>
                ) : (
                  <div className="max-w-[75%] rounded-2xl rounded-bl-md border border-line bg-panel px-3.5 py-2 text-sm text-muted">
                    這題我沒梗 🤷（圖庫還不夠多，之後會更好）
                  </div>
                )}
              </div>
            ),
          )
        )}

        {sending && (
          <div className="flex justify-start">
            <div className="flex items-center gap-1.5 rounded-2xl rounded-bl-md border border-line bg-panel px-3.5 py-2.5 text-muted">
              <span className="size-1.5 animate-bounce rounded-full bg-muted [animation-delay:-0.2s]" />
              <span className="size-1.5 animate-bounce rounded-full bg-muted [animation-delay:-0.1s]" />
              <span className="size-1.5 animate-bounce rounded-full bg-muted" />
            </div>
          </div>
        )}
        <div ref={endRef} />
      </div>

      <div className="flex items-center gap-2 border-t border-line bg-panel px-4 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
        {messages.length > 0 && (
          <button
            onClick={clear}
            className="grid size-9 shrink-0 place-items-center rounded-full text-muted active:bg-raised"
            aria-label="清空對話"
          >
            <Trash2 className="size-4" />
          </button>
        )}
        <input
          value={input}
          maxLength={500}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
          placeholder="說點什麼，他會用梗圖回你…"
          className="min-w-0 flex-1 rounded-full border border-line bg-ink px-4 py-2.5 text-sm outline-none focus:border-amber"
        />
        <button
          onClick={send}
          disabled={!input.trim() || sending}
          className="grid size-10 shrink-0 place-items-center rounded-full bg-amber text-ink active:opacity-80 disabled:opacity-40"
          aria-label="送出"
        >
          {sending ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}
        </button>
      </div>
    </div>
  );
}
