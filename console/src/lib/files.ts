/** 讀取圖片檔為 base64（去掉 data URL 前綴），供上傳 API 使用。 */
export async function fileToBase64(file: File): Promise<string> {
  const dataUrl = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(new Error("讀取檔案失敗"));
    reader.readAsDataURL(file);
  });
  return dataUrl.slice(dataUrl.indexOf(",") + 1);
}

/**
 * 縮圖後回傳 base64（不含 data URL 前綴），供「推薦/OCR」上傳用。
 *
 * 手機原始像素的高解析/長截圖(20MP+、20MB+ payload)會撐爆 gateway → 502。
 * 先縮到 ~maxPixels 並重壓成 JPEG，payload 從數十 MB 降到 1–2MB，上傳與 OCR 都快，
 * 文字仍清楚可辨。瀏覽器不支援 canvas 途徑時退回原圖（後端還有防呆縮圖）。
 */
export async function downscaleToBase64(
  file: File,
  { maxPixels = 6_000_000, maxSide = 4096, quality = 0.85 } = {},
): Promise<string> {
  let bitmap: ImageBitmap;
  try {
    bitmap = await createImageBitmap(file);
  } catch {
    return fileToBase64(file); // 不支援 createImageBitmap → 交給後端縮
  }
  try {
    const { width: w, height: h } = bitmap;
    const scale = Math.min(1, Math.sqrt(maxPixels / (w * h)), maxSide / Math.max(w, h));
    const dw = Math.max(1, Math.round(w * scale));
    const dh = Math.max(1, Math.round(h * scale));
    const canvas = document.createElement("canvas");
    canvas.width = dw;
    canvas.height = dh;
    const ctx = canvas.getContext("2d");
    if (!ctx) return fileToBase64(file);
    ctx.drawImage(bitmap, 0, 0, dw, dh);
    const dataUrl = canvas.toDataURL("image/jpeg", quality);
    return dataUrl.slice(dataUrl.indexOf(",") + 1);
  } finally {
    bitmap.close?.();
  }
}

const IMAGE_TYPES = new Set(["image/png", "image/jpeg", "image/webp"]);

/** 從拖曳 / 選檔結果篩出支援的圖片（PNG / JPEG / WebP）。 */
export function imageFilesFrom(list: FileList | File[] | null | undefined): File[] {
  return Array.from(list ?? []).filter((f) => IMAGE_TYPES.has(f.type));
}
