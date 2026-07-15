import { Bookmark } from "lucide-react";
import { useState } from "react";
import { toggleFavorite } from "../lib/api";
import { useCurrentUser } from "../lib/auth";

/** 收藏鈕：只有登入使用者看得到；樂觀切換，失敗回滾。 */
export default function FavoriteButton({
  memeId,
  initial = false,
  className = "",
  label = false,
}: {
  memeId: string;
  initial?: boolean;
  className?: string;
  label?: boolean;
}) {
  const user = useCurrentUser();
  const [on, setOn] = useState(initial);
  const [busy, setBusy] = useState(false);
  if (!user) return null; // 未登入不顯示

  const click = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (busy) return;
    const next = !on;
    setOn(next);
    setBusy(true);
    try {
      await toggleFavorite(memeId, next);
    } catch {
      setOn(!next); // 失敗回滾
    } finally {
      setBusy(false);
    }
  };

  return (
    <button onClick={click} aria-pressed={on} aria-label={on ? "取消收藏" : "收藏"} className={className}>
      <Bookmark
        className={`size-4 ${on ? "fill-current text-amber" : ""}`}
        strokeWidth={on ? 2.2 : 1.75}
      />
      {label && <span>{on ? "已收藏" : "收藏"}</span>}
    </button>
  );
}
