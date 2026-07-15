import {
  Loader2,
  MessageCircle,
  RefreshCw,
  Send,
  ThumbsDown,
  ThumbsUp,
  Trash2,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import MemeImage from "../components/MemeImage";
import ShareButton from "../components/ShareButton";
import { chat, fetchGallery, sendChatFeedback } from "../lib/api";
import { logBreadcrumb } from "../lib/breadcrumbs";
import { randomBotName } from "../lib/nickname";
import type { ChatMeme } from "../types";

/** 只會回梗圖的朋友：你打字，他每則都丟一張梗圖回你（embedding 檢索、秒回）。 */

type ChatMsg =
  | { role: "user"; id: string; text: string }
  | { role: "bot"; id: string; meme: ChatMeme | null; rating?: "up" | "down" };

const STORAGE_KEY = "memeradar.chatHistory";
const BOT_NAME_KEY = "memeradar.chatBotName";
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

// 每個 session 一個隨機搞笑名字（存起來 → 同一場對話跨重整不變；清空/換一個才會變）
function loadBotName(): string {
  try {
    const n = localStorage.getItem(BOT_NAME_KEY);
    if (n) return n;
    const fresh = randomBotName();
    localStorage.setItem(BOT_NAME_KEY, fresh);
    return fresh;
  } catch {
    return randomBotName();
  }
}

export default function ChatScreen() {
  const [messages, setMessages] = useState<ChatMsg[]>(load);
  const [botName, setBotName] = useState(loadBotName);
  const [avatar, setAvatar] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  // 頭像＝以名字為 seed 從圖庫抓一張梗圖（同名字 → 同頭像；換名字 → 換頭像）
  useEffect(() => {
    let alive = true;
    fetchGallery(botName, 0, 1)
      .then((items) => alive && setAvatar(items[0]?.image_url ?? null))
      .catch(() => alive && setAvatar(null));
    return () => {
      alive = false;
    };
  }, [botName]);

  const newBotName = () => {
    const fresh = randomBotName();
    setBotName(fresh);
    try {
      localStorage.setItem(BOT_NAME_KEY, fresh);
    } catch {
      /* ignore */
    }
  };

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
    logBreadcrumb("action", "梗友：送訊息");
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
    newBotName(); // 清空 = 新 session → 換個新名字
    try {
      localStorage.removeItem(STORAGE_KEY);
    } catch {
      /* ignore */
    }
  };

  // 評價一則回覆：更新本地狀態（存起來）+ 回報後端（帶觸發的訊息供優化）
  const rate = (msgId: string, memeId: string, trigger: string, rating: "up" | "down") => {
    setMessages((ms) =>
      ms.map((m) => (m.id === msgId && m.role === "bot" ? { ...m, rating } : m)),
    );
    sendChatFeedback(memeId, trigger, rating);
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center gap-2.5 border-b border-line px-4 py-2">
        {avatar ? (
          <MemeImage src={avatar} alt="" className="size-8 shrink-0 rounded-full object-cover" />
        ) : (
          <div className="grid size-8 shrink-0 place-items-center rounded-full bg-amber-soft">
            <MessageCircle className="size-4 text-amber" strokeWidth={2} />
          </div>
        )}
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold">{botName}</p>
          <p className="text-[11px] text-muted">只會回梗圖的朋友 · 上線中</p>
        </div>
        <button
          onClick={newBotName}
          className="flex items-center gap-1 rounded-full px-2 py-1 text-[11px] text-muted active:bg-raised"
          aria-label="換一個名字"
        >
          <RefreshCw className="size-3" /> 換一個
        </button>
      </div>

      <div className="flex-1 space-y-3 overflow-y-auto px-4 py-4">
        {messages.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
            {avatar ? (
              <MemeImage src={avatar} alt="" className="size-20 rounded-full object-cover" />
            ) : (
              <div className="grid size-16 place-items-center rounded-full bg-amber-soft">
                <MessageCircle className="size-8 text-amber" strokeWidth={1.75} />
              </div>
            )}
            <p className="text-base font-semibold">
              嗨，我是 <span className="text-amber">{botName}</span>
            </p>
            <p className="max-w-[16rem] text-sm leading-relaxed text-muted">
              你說什麼我都<span className="text-amber">丟梗圖</span>回你 😌
            </p>
          </div>
        ) : (
          messages.map((m, i) => {
            if (m.role === "user") {
              return (
                <div key={m.id} className="flex justify-end animate-fade-in-up">
                  <div className="max-w-[75%] rounded-2xl rounded-br-md bg-amber px-3.5 py-2 text-sm text-ink">
                    {m.text}
                  </div>
                </div>
              );
            }
            // 觸發這則回覆的使用者訊息（供優化）
            const prev = messages[i - 1];
            const trigger = prev && prev.role === "user" ? prev.text : "";
            return (
              <div key={m.id} className="flex flex-col items-start gap-1 animate-fade-in-up">
                {m.meme ? (
                  <>
                    <div className="max-w-[75%] overflow-hidden rounded-2xl rounded-bl-md border border-line bg-panel">
                      <MemeImage
                        src={m.meme.image_url}
                        alt={m.meme.ocr_text ?? "梗圖"}
                        className="max-h-[42vh] w-full object-contain"
                      />
                    </div>
                    <div className="flex items-center gap-0.5 pl-1">
                      <button
                        onClick={() => rate(m.id, m.meme!.meme_id, trigger, "up")}
                        aria-label="這張讚"
                        className={`grid size-7 place-items-center rounded-full active:scale-90 ${
                          m.rating === "up" ? "text-signal" : "text-muted"
                        }`}
                      >
                        <ThumbsUp
                          className={`size-4 ${m.rating === "up" ? "animate-pop fill-current" : ""}`}
                          strokeWidth={m.rating === "up" ? 2.4 : 1.75}
                        />
                      </button>
                      <button
                        onClick={() => rate(m.id, m.meme!.meme_id, trigger, "down")}
                        aria-label="這張不行"
                        className={`grid size-7 place-items-center rounded-full active:scale-90 ${
                          m.rating === "down" ? "text-danger" : "text-muted"
                        }`}
                      >
                        <ThumbsDown
                          className={`size-4 ${m.rating === "down" ? "animate-pop fill-current" : ""}`}
                          strokeWidth={m.rating === "down" ? 2.4 : 1.75}
                        />
                      </button>
                      <ShareButton
                        memeId={m.meme.meme_id}
                        className="grid size-7 place-items-center rounded-full text-muted active:scale-90"
                      />
                    </div>
                  </>
                ) : (
                  <div className="max-w-[75%] rounded-2xl rounded-bl-md border border-line bg-panel px-3.5 py-2 text-sm text-muted">
                    這題我沒梗 🤷（圖庫還不夠多，之後會更好）
                  </div>
                )}
              </div>
            );
          })
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
