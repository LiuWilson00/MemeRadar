import { useSyncExternalStore } from "react";
import type { User } from "../types";

/** 前台使用者登入狀態（Google 登入 → 後端簽的 session token）。
 * 存 localStorage；token 帶在 API 的 Authorization: Bearer。與後台 admin（Basic）互不相干。 */

const TOKEN_KEY = "memeradar.userToken";
const USER_KEY = "memeradar.user";

export const GOOGLE_CLIENT_ID =
  (import.meta.env.VITE_GOOGLE_CLIENT_ID as string | undefined) ?? "";

export function getUserToken(): string | null {
  if (typeof localStorage === "undefined") return null;
  return localStorage.getItem(TOKEN_KEY);
}

function readUser(): User | null {
  if (typeof localStorage === "undefined") return null;
  const raw = localStorage.getItem(USER_KEY);
  if (!raw) return null;
  try {
    return JSON.parse(raw) as User;
  } catch {
    return null;
  }
}

export function saveSession(token: string, user: User): void {
  if (typeof localStorage === "undefined") return;
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.setItem(USER_KEY, JSON.stringify(user));
  emit();
}

export function clearSession(): void {
  if (typeof localStorage === "undefined") return;
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
  emit();
}

// 同分頁即時更新（跨分頁靠瀏覽器的 storage 事件）
const listeners = new Set<() => void>();
function emit() {
  for (const l of listeners) l();
}
function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  if (typeof window !== "undefined") window.addEventListener("storage", listener);
  return () => {
    listeners.delete(listener);
    if (typeof window !== "undefined") window.removeEventListener("storage", listener);
  };
}

/** React：目前登入使用者（未登入回 null），登入/登出即時反映。 */
export function useCurrentUser(): User | null {
  return useSyncExternalStore(subscribe, readUser, () => null);
}
