import { beforeEach, describe, expect, it } from "vitest";
import { getClientId } from "./clientId";

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

describe("getClientId", () => {
  it("首次呼叫產生並存下非空匿名碼", () => {
    const id = getClientId();
    expect(id).toBeTruthy();
    expect(localStorage.getItem("memeradar.clientId")).toBe(id);
  });

  it("再次呼叫回傳同一個（穩定識別同一支手機）", () => {
    const first = getClientId();
    const second = getClientId();
    expect(second).toBe(first);
  });

  it("沿用 localStorage 既有的值", () => {
    localStorage.setItem("memeradar.clientId", "c_existing");
    expect(getClientId()).toBe("c_existing");
  });
});
