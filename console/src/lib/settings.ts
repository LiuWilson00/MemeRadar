import type { Filters } from "../types";

/** 手機 client 的使用者偏好（存 localStorage，套用到每次推薦）。 */
export interface UserSettings {
  excludeNsfw: boolean;
  franchises: string[]; // 偏好梗圖包（空 = 不限）
  categories: string[]; // 偏好分類（空 = 不限）
  fastMode: boolean; // 快速模式：OCR/CLIP 秒回、跳過 VLM（預設開；關掉走 AI 精讀）
}

export const DEFAULT_SETTINGS: UserSettings = {
  excludeNsfw: true,
  franchises: [],
  categories: [],
  fastMode: true,
};

const KEY = "memeradar.settings.v1";

function strArray(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((x): x is string => typeof x === "string") : [];
}

export function loadSettings(): UserSettings {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return { ...DEFAULT_SETTINGS };
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    return {
      excludeNsfw:
        typeof parsed.excludeNsfw === "boolean" ? parsed.excludeNsfw : DEFAULT_SETTINGS.excludeNsfw,
      franchises: strArray(parsed.franchises),
      categories: strArray(parsed.categories),
      fastMode:
        typeof parsed.fastMode === "boolean" ? parsed.fastMode : DEFAULT_SETTINGS.fastMode,
    };
  } catch {
    return { ...DEFAULT_SETTINGS };
  }
}

export function saveSettings(settings: UserSettings): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(settings));
  } catch {
    /* localStorage 不可用（無痕 / 配額）時靜默略過 */
  }
}

export function settingsToFilters(settings: UserSettings): Filters {
  return {
    franchises: settings.franchises,
    categories: settings.categories,
    exclude_nsfw: settings.excludeNsfw,
  };
}
