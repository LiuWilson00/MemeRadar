import { Bookmark, ChevronLeft, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import MemeImage from "../components/MemeImage";
import { fetchFavorites } from "../lib/api";
import type { GalleryItem } from "../types";
import GalleryDetail from "./GalleryDetail";

/** 我的收藏：登入使用者收藏的梗圖（全螢幕覆蓋）；點一張進 detail（可讚踩/留言/分享）。 */
export default function FavoritesScreen({ onClose }: { onClose: () => void }) {
  const [items, setItems] = useState<GalleryItem[] | null>(null);
  const [detail, setDetail] = useState<GalleryItem | null>(null);

  useEffect(() => {
    fetchFavorites()
      .then(setItems)
      .catch(() => setItems([]));
  }, []);

  const patch = (memeId: string, likes: number, liked: boolean) =>
    setItems((prev) => prev?.map((it) => (it.meme_id === memeId ? { ...it, likes, liked } : it)) ?? prev);

  return (
    <div className="fixed inset-0 z-40 flex flex-col bg-ink animate-fade-in">
      <div className="flex items-center gap-2 border-b border-line px-3 py-2 pt-[max(0.5rem,env(safe-area-inset-top))]">
        <button
          onClick={onClose}
          className="flex items-center gap-0.5 rounded-full px-2 py-1 text-sm text-muted active:bg-panel"
          aria-label="返回"
        >
          <ChevronLeft className="size-5" /> 返回
        </button>
        <p className="text-sm font-semibold">我的收藏</p>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        {items === null ? (
          <div className="flex justify-center p-10">
            <Loader2 className="size-6 animate-spin text-muted" />
          </div>
        ) : items.length === 0 ? (
          <div className="flex flex-col items-center gap-2 p-12 text-center text-muted">
            <Bookmark className="size-9" strokeWidth={1.5} />
            <p className="text-sm">還沒有收藏</p>
            <p className="text-xs">在梗圖詳情頁點「收藏」，就會出現在這裡。</p>
          </div>
        ) : (
          <div className="columns-2 gap-2 [&>*]:mb-2">
            {items.map((it) => (
              <button
                key={it.meme_id}
                onClick={() => setDetail(it)}
                className="block w-full overflow-hidden rounded-xl border border-line bg-panel active:scale-[0.99]"
              >
                <MemeImage
                  src={it.image_url}
                  alt={it.ocr_text ?? "梗圖"}
                  aspectRatio={it.width && it.height ? it.width / it.height : undefined}
                  className="w-full object-cover"
                />
              </button>
            ))}
          </div>
        )}
      </div>

      {detail && (
        <GalleryDetail item={detail} onClose={() => setDetail(null)} onChange={patch} />
      )}
    </div>
  );
}
