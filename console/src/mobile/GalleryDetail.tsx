import { ChevronLeft, Heart, Loader2, MessageCircle, Pencil, Send, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import MemeImage from "../components/MemeImage";
import { addComment, deleteComment, editComment, fetchComments, toggleLike } from "../lib/api";
import { useCurrentUser } from "../lib/auth";
import { displayName } from "../lib/nickname";
import type { GalleryItem, MemeComment } from "../types";

/** 探索圖庫詳細：大圖 + 飄動彈幕（留言）+ 下方留言列表 / 輸入。彈幕只在這裡看得到。 */
export default function GalleryDetail({
  item,
  onClose,
  onChange,
}: {
  item: GalleryItem;
  onClose: () => void;
  onChange?: (memeId: string, likes: number, liked: boolean) => void;
}) {
  const user = useCurrentUser();
  const [likes, setLikes] = useState(item.likes);
  const [liked, setLiked] = useState(item.liked);
  const [comments, setComments] = useState<MemeComment[] | null>(null);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [editing, setEditing] = useState<{ id: string; text: string } | null>(null);
  const listEnd = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetchComments(item.meme_id)
      .then(setComments)
      .catch(() => setComments([]));
  }, [item.meme_id]);

  const like = async () => {
    // 樂觀更新
    const next = !liked;
    setLiked(next);
    setLikes((n) => n + (next ? 1 : -1));
    try {
      const r = await toggleLike(item.meme_id);
      setLikes(r.likes);
      setLiked(r.liked);
      onChange?.(item.meme_id, r.likes, r.liked);
    } catch {
      setLiked(!next); // 回滾
      setLikes((n) => n + (next ? -1 : 1));
    }
  };

  const send = async () => {
    const body = text.trim().slice(0, 80);
    if (!body || sending) return;
    setSending(true);
    try {
      const created = await addComment(item.meme_id, displayName(user), body);
      setComments((cs) => [...(cs ?? []), created]);
      setText("");
      requestAnimationFrame(() => listEnd.current?.scrollIntoView({ behavior: "smooth" }));
    } finally {
      setSending(false);
    }
  };

  const saveEdit = async () => {
    if (!editing) return;
    const body = editing.text.trim().slice(0, 80);
    if (!body) return;
    await editComment(item.meme_id, editing.id, body);
    setComments((cs) =>
      (cs ?? []).map((c) => (c.comment_id === editing.id ? { ...c, text: body, edited: true } : c)),
    );
    setEditing(null);
  };

  const remove = async (id: string) => {
    await deleteComment(item.meme_id, id);
    setComments((cs) => (cs ?? []).filter((c) => c.comment_id !== id));
  };

  const danmaku = comments ?? [];

  return (
    <div className="fixed inset-0 z-40 flex flex-col bg-ink animate-fade-in">
      <div className="mx-auto flex min-h-[100dvh] w-full max-w-md flex-col">
        <header className="flex items-center gap-1 px-2 pb-2 pt-[max(0.75rem,env(safe-area-inset-top))]">
          <button
            onClick={onClose}
            className="flex items-center gap-1 rounded-full px-2.5 py-1.5 text-sm text-fg active:bg-panel"
            aria-label="返回圖庫"
          >
            <ChevronLeft className="size-5" /> 返回
          </button>
        </header>

        <div className="flex min-h-0 flex-1 flex-col overflow-y-auto px-4 pb-4">
          {/* 圖 + 飄動彈幕 */}
          <div className="relative overflow-hidden rounded-2xl border border-line bg-ink">
            <MemeImage
              src={item.image_url}
              alt={item.ocr_text ?? "梗圖"}
              className="max-h-[52vh] w-full object-contain"
            />
            {danmaku.length > 0 && (
              <div className="pointer-events-none absolute inset-0 overflow-hidden">
                {danmaku.map((c, i) => (
                  <span
                    key={c.comment_id}
                    className="danmaku-item rounded-full bg-ink/65 px-2.5 py-1 text-xs text-fg"
                    style={{
                      top: `${6 + (i % 5) * 17}%`,
                      animationDuration: `${9 + (i % 4) * 2}s`,
                      animationDelay: `${(i % 6) * 1.4}s`,
                    }}
                  >
                    {c.text}
                  </span>
                ))}
              </div>
            )}
          </div>

          {/* 讚 + 資訊 */}
          <div className="mt-3 flex items-center gap-3">
            <button
              onClick={like}
              className={`flex items-center gap-1.5 rounded-full border px-4 py-2 text-sm active:scale-95 ${
                liked ? "border-danger bg-danger/15 text-danger" : "border-line text-fg"
              }`}
              aria-pressed={liked}
            >
              <Heart className={`size-4 ${liked ? "animate-pop fill-current" : ""}`} /> {likes}
            </button>
            <span className="flex items-center gap-1.5 text-sm text-muted">
              <MessageCircle className="size-4" /> {danmaku.length}
            </span>
            {item.franchise && <span className="ml-auto text-xs text-muted">{item.franchise}</span>}
          </div>

          {item.ocr_text?.trim() && (
            <p className="mt-2 text-xs leading-relaxed text-muted">{item.ocr_text}</p>
          )}

          {/* 留言列表 */}
          <div className="mt-4 flex flex-col gap-2">
            <p className="text-xs font-semibold text-muted">彈幕留言</p>
            {comments === null ? (
              <div className="flex justify-center py-6">
                <Loader2 className="size-5 animate-spin text-muted" />
              </div>
            ) : comments.length === 0 ? (
              <p className="py-4 text-center text-xs text-muted">還沒有人留言，來當第一個 👀</p>
            ) : (
              comments.map((c) => (
                <div key={c.comment_id} className="rounded-2xl border border-line bg-panel px-3 py-2">
                  {editing?.id === c.comment_id ? (
                    <div className="flex flex-col gap-2">
                      <input
                        value={editing.text}
                        maxLength={80}
                        onChange={(e) => setEditing({ id: c.comment_id, text: e.target.value })}
                        className="rounded-xl border border-line bg-ink px-3 py-2 text-sm outline-none focus:border-amber"
                      />
                      <div className="flex gap-2">
                        <button
                          onClick={saveEdit}
                          className="rounded-full bg-amber px-4 py-1 text-xs font-semibold text-ink active:opacity-80"
                        >
                          儲存
                        </button>
                        <button
                          onClick={() => setEditing(null)}
                          className="rounded-full border border-line px-4 py-1 text-xs text-muted"
                        >
                          取消
                        </button>
                      </div>
                    </div>
                  ) : (
                    <>
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-semibold text-amber">{c.author_name}</span>
                        {c.edited && <span className="text-[10px] text-muted">已編輯</span>}
                        {c.mine && (
                          <span className="ml-auto flex gap-1.5">
                            <button
                              onClick={() => setEditing({ id: c.comment_id, text: c.text })}
                              className="text-muted active:text-fg"
                              aria-label="編輯"
                            >
                              <Pencil className="size-3.5" />
                            </button>
                            <button
                              onClick={() => remove(c.comment_id)}
                              className="text-muted active:text-danger"
                              aria-label="刪除"
                            >
                              <Trash2 className="size-3.5" />
                            </button>
                          </span>
                        )}
                      </div>
                      <p className="mt-0.5 break-words text-sm text-fg">{c.text}</p>
                    </>
                  )}
                </div>
              ))
            )}
            <div ref={listEnd} />
          </div>
        </div>

        {/* 輸入列 */}
        <div className="flex items-center gap-2 border-t border-line bg-panel px-4 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))]">
          <input
            value={text}
            maxLength={80}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && send()}
            placeholder={`以「${displayName(user)}」留一則彈幕…`}
            className="min-w-0 flex-1 rounded-full border border-line bg-ink px-4 py-2.5 text-sm outline-none focus:border-amber"
          />
          <button
            onClick={send}
            disabled={!text.trim() || sending}
            className="grid size-10 shrink-0 place-items-center rounded-full bg-amber text-ink active:opacity-80 disabled:opacity-40"
            aria-label="送出"
          >
            {sending ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}
          </button>
        </div>
      </div>
    </div>
  );
}
