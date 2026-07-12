import type { Filters } from "../types";

/** 手機 client 的使用者偏好（存 localStorage，套用到每次推薦）。 */
export interface UserSettings {
  excludeNsfw: boolean;
  franchises: string[]; // 偏好梗圖包（空 = 不限）
  categories: string[]; // 偏好分類（空 = 不限）
}

export const DEFAULT_SETTINGS: UserSettings = {
  excludeNsfw: true,
  franchises: [],
  categories: [],
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
