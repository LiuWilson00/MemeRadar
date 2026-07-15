import { Heart, ImageOff, Loader2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import MemeImage from "../components/MemeImage";
import { fetchGallery, toggleLike } from "../lib/api";
import type { GalleryItem } from "../types";
import GalleryDetail from "./GalleryDetail";

const PAGE = 24;

/** 探索圖庫：Pinterest 式瀑布流 + 無限捲動；卡片可直接按讚，點開看彈幕。 */
export default function ExploreScreen() {
  // 每次開頁固定一個 seed → 隨機排序在分頁間穩定、不重複不跳號
  const [seed] = useState(() => Math.random().toString(36).slice(2, 10));
  const [items, setItems] = useState<GalleryItem[]>([]);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const [done, setDone] = useState(false);
  const [error, setError] = useState(false);
  const [detail, setDetail] = useState<GalleryItem | null>(null);
  const sentinel = useRef<HTMLDivElement>(null);
  const busy = useRef(false);

  const loadMore = useCallback(async () => {
    if (busy.current || done) return;
    busy.current = true;
    setLoading(true);
    try {
      const page = await fetchGallery(seed, offset, PAGE);
      setItems((prev) => [...prev, ...page]);
      setOffset((o) => o + page.length);
      if (page.length < PAGE) setDone(true);
    } catch {
      setError(true);
      setDone(true);
    } finally {
      setLoading(false);
      busy.current = false;
    }
  }, [seed, offset, done]);

  useEffect(() => {
    void loadMore();
    // 只在掛載時載第一頁；後續由觀察器觸發
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const el = sentinel.current;
    if (!el) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting) void loadMore();
      },
      { rootMargin: "500px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [loadMore]);

  const patch = (memeId: string, likes: number, liked: boolean) =>
    setItems((prev) => prev.map((x) => (x.meme_id === memeId ? { ...x, likes, liked } : x)));

  const likeCard = async (e: React.MouseEvent, it: GalleryItem) => {
    e.stopPropagation();
    patch(it.meme_id, it.likes + (it.liked ? -1 : 1), !it.liked); // 樂觀
    try {
      const r = await toggleLike(it.meme_id);
      patch(it.meme_id, r.likes, r.liked);
    } catch {
      patch(it.meme_id, it.likes, it.liked); // 回滾
    }
  };

  return (
    <div className="flex-1 overflow-y-auto px-3 pt-3 pb-[max(1rem,env(safe-area-inset-bottom))]">
      <p className="px-1 pb-2 text-xs text-muted">大家的梗圖庫 —— 逛一逛、按讚、留彈幕。</p>

      {items.length === 0 && loading ? (
        <div className="flex justify-center py-16">
          <Loader2 className="size-6 animate-spin text-muted" />
        </div>
      ) : items.length === 0 ? (
        <div className="flex flex-col items-center gap-2 py-16 text-center text-muted">
          <ImageOff className="size-8" strokeWidth={1.5} />
          <p className="text-sm">圖庫還空空的，之後上傳的梗圖會出現在這</p>
        </div>
      ) : (
        <div className="columns-2 gap-2 [&>*]:mb-2">
          {items.map((it) => (
            <button
              key={it.meme_id}
              onClick={() => setDetail(it)}
              className="block w-full break-inside-avoid overflow-hidden rounded-2xl border border-line
                         bg-panel text-left transition-transform active:scale-[0.98]"
            >
              <div className="relative">
                <MemeImage
                  src={it.image_url}
                  alt={it.ocr_text ?? "梗圖"}
                  className="w-full object-cover"
                  aspectRatio={it.width && it.height ? it.width / it.height : undefined}
                />
                <span
                  role="button"
                  aria-label={it.liked ? "取消讚" : "讚"}
                  onClick={(e) => likeCard(e, it)}
                  className={`absolute bottom-2 right-2 flex items-center gap-1 rounded-full px-2 py-1
                              text-xs backdrop-blur-sm ${
                                it.liked ? "bg-danger/80 text-white" : "bg-ink/70 text-fg"
                              }`}
                >
                  <Heart className={`size-3.5 ${it.liked ? "fill-current" : ""}`} /> {it.likes}
                </span>
              </div>
              {it.ocr_text?.trim() && (
                <p className="truncate px-2.5 py-1.5 text-xs text-muted">{it.ocr_text}</p>
              )}
            </button>
          ))}
        </div>
      )}

      {!done && <div ref={sentinel} className="h-8" />}
      {loading && items.length > 0 && (
        <div className="flex justify-center py-4">
          <Loader2 className="size-5 animate-spin text-muted" />
        </div>
      )}
      {done && !error && items.length > 0 && (
        <p className="py-4 text-center text-xs text-muted">到底了 🏁</p>
      )}
      {error && <p className="py-3 text-center text-xs text-danger">載入失敗，稍後再試</p>}

      {detail && (
        <GalleryDetail item={detail} onClose={() => setDetail(null)} onChange={patch} />
      )}
    </div>
  );
}
