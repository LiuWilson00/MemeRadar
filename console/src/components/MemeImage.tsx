import { useState } from "react";

/** 梗圖顯示元件：載入失敗時顯示可點擊重試的佔位，避免破圖 icon + 版面壓扁。
 *
 * 後端 API 在開發時常重啟（BGE-M3 暖機約 60s），空窗期 <img> 載入失敗後
 * 瀏覽器不會自動重試——這裡提供手動重試（cache-bust 強制重抓），並固定
 * 最小高度讓卡片版面在失敗時仍穩定。
 */
export default function MemeImage({
  src,
  alt,
  className,
  href,
}: {
  src: string;
  alt: string;
  className?: string;
  href?: string;
}) {
  const [failed, setFailed] = useState(false);
  const [reload, setReload] = useState(0);

  if (failed) {
    return (
      // 純 div（非 button/a）以便安全巢狀在外層的 <a>/<button> 內
      <div
        onClick={(e) => {
          e.preventDefault();
          e.stopPropagation();
          setFailed(false);
          setReload((k) => k + 1);
        }}
        title="重新載入圖片"
        className={`flex min-h-24 cursor-pointer flex-col items-center justify-center gap-1
                    text-muted ${className ?? ""}`}
      >
        <span className="text-2xl" aria-hidden>
          🖼
        </span>
        <span className="text-[11px]">圖片載入失敗 · 點此重試</span>
      </div>
    );
  }

  const img = (
    <img
      src={reload ? `${src}${src.includes("?") ? "&" : "?"}r=${reload}` : src}
      alt={alt}
      className={className}
      loading="lazy"
      onError={() => setFailed(true)}
    />
  );

  return href ? (
    <a href={href} target="_blank" rel="noreferrer" title="開新分頁檢視原圖">
      {img}
    </a>
  ) : (
    img
  );
}
