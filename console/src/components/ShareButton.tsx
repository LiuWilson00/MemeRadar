import { Check, Share2 } from "lucide-react";
import { useState } from "react";
import { shareOrCopy } from "../lib/api";

/** 分享/複製梗圖：手機叫原生分享面板；網頁把「圖片」複製到剪貼簿（可直接貼進聊天），
 *  不支援時退回複製連結。 */
export default function ShareButton({
  memeId,
  className = "",
  label = false,
}: {
  memeId: string;
  className?: string;
  label?: boolean;
}) {
  const [msg, setMsg] = useState<string | null>(null);

  const onClick = async (e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      const result = await shareOrCopy(memeId);
      if (result === "image" || result === "link") {
        setMsg(result === "image" ? "已複製圖片" : "已複製連結");
        setTimeout(() => setMsg(null), 1600);
      }
    } catch {
      /* 剪貼簿不可用等 → 靜默 */
    }
  };

  return (
    <button onClick={onClick} aria-label="分享 / 複製梗圖" className={className}>
      {msg ? <Check className="size-4 text-signal" /> : <Share2 className="size-4" />}
      {label && <span>{msg ?? "分享"}</span>}
    </button>
  );
}
