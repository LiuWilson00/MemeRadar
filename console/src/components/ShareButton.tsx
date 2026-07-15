import { Check, Share2 } from "lucide-react";
import { useState } from "react";
import { shareOrCopy } from "../lib/api";

/** 分享/複製梗圖連結：手機叫原生分享面板（可直接送 Line），否則複製到剪貼簿。 */
export default function ShareButton({
  memeId,
  className = "",
  label = false,
}: {
  memeId: string;
  className?: string;
  label?: boolean;
}) {
  const [copied, setCopied] = useState(false);

  const onClick = async (e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      const result = await shareOrCopy(memeId);
      if (result === "copied") {
        setCopied(true);
        setTimeout(() => setCopied(false), 1500);
      }
    } catch {
      /* 剪貼簿不可用等 → 靜默 */
    }
  };

  return (
    <button onClick={onClick} aria-label="分享 / 複製連結" className={className}>
      {copied ? <Check className="size-4 text-signal" /> : <Share2 className="size-4" />}
      {label && <span>{copied ? "已複製" : "分享"}</span>}
    </button>
  );
}
