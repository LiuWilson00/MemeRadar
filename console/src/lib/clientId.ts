/** 匿名 client 識別碼（存 localStorage，隨請求送出供回饋分群分析）。
 *
 * 純隨機、無任何個資——只是為了能分辨「同一支手機的多次互動」，
 * 讓未來能用回饋做 per-user 分析 / 去重。可在設定頁清除。
 */
const KEY = "memeradar.clientId";

function randomId(): string {
  const uuid = globalThis.crypto?.randomUUID?.();
  if (uuid) return `c_${uuid.replace(/-/g, "").slice(0, 24)}`;
  return `c_${Math.random().toString(36).slice(2)}${Math.random().toString(36).slice(2, 8)}`;
}

export function getClientId(): string {
  try {
    let id = localStorage.getItem(KEY);
    if (!id) {
      id = randomId();
      localStorage.setItem(KEY, id);
    }
    return id;
  } catch {
    return "anon"; // localStorage 不可用（無痕）→ 固定匿名值，不影響推薦
  }
}
