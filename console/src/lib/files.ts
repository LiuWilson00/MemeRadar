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

const IMAGE_TYPES = new Set(["image/png", "image/jpeg", "image/webp"]);

/** 從拖曳 / 選檔結果篩出支援的圖片（PNG / JPEG / WebP）。 */
export function imageFilesFrom(list: FileList | File[] | null | undefined): File[] {
  return Array.from(list ?? []).filter((f) => IMAGE_TYPES.has(f.type));
}
