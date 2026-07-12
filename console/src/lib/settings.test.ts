import { beforeEach, describe, expect, it } from "vitest";
import { DEFAULT_SETTINGS, loadSettings, saveSettings, settingsToFilters } from "./settings";

class MemStorage {
  private m = new Map<string, string>();
  getItem(k: string) {
    return this.m.has(k) ? this.m.get(k)! : null;
  }
  setItem(k: string, v: string) {
    this.m.set(k, v);
  }
  removeItem(k: string) {
    this.m.delete(k);
  }
  clear() {
    this.m.clear();
  }
}

beforeEach(() => {
  (globalThis as unknown as { localStorage: MemStorage }).localStorage = new MemStorage();
});

describe("settings persistence", () => {
  it("空 localStorage 回傳預設值（排除成人、不限梗圖包）", () => {
    expect(loadSettings()).toEqual(DEFAULT_SETTINGS);
    expect(DEFAULT_SETTINGS.excludeNsfw).toBe(true);
  });

  it("存檔後讀回一致", () => {
    saveSettings({ excludeNsfw: false, franchises: ["海綿寶寶"], categories: ["卡通動畫"] });
    expect(loadSettings()).toEqual({
      excludeNsfw: false,
      franchises: ["海綿寶寶"],
      categories: ["卡通動畫"],
    });
  });

  it("壞掉的 JSON 退回預設，不拋例外", () => {
    localStorage.setItem("memeradar.settings.v1", "{not json");
    expect(loadSettings()).toEqual(DEFAULT_SETTINGS);
  });

  it("缺欄位以預設補齊、型別不符者忽略", () => {
    localStorage.setItem(
      "memeradar.settings.v1",
      JSON.stringify({ franchises: ["甄嬛傳", 123], excludeNsfw: "yes" }),
    );
    const s = loadSettings();
    expect(s.franchises).toEqual(["甄嬛傳"]); // 非字串被濾掉
    expect(s.excludeNsfw).toBe(true); // 型別不符 → 預設
    expect(s.categories).toEqual([]);
  });

  it("settingsToFilters 對齊推薦 API 的 filters 契約", () => {
    expect(
      settingsToFilters({ excludeNsfw: true, franchises: ["海綿寶寶"], categories: [] }),
    ).toEqual({ franchises: ["海綿寶寶"], categories: [], exclude_nsfw: true });
  });
});
