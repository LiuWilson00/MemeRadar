import { describe, expect, it } from "vitest";
import { apiFetch, buildRecommendRequest, buildTaskRequest, DEFAULT_FILTERS, DEFAULT_PARAMS } from "./api";

describe("buildRecommendRequest", () => {
  it("組出符合 01 §5.2 契約的請求", () => {
    const body = buildRecommendRequest(
      [{ speaker: "other", text: "你報告又遲交了！" }],
      { ...DEFAULT_FILTERS, franchises: ["海綿寶寶"] },
      { ...DEFAULT_PARAMS, top_n: 3 },
    );
    expect(body).toEqual({
      input_type: "text",
      conversation: [{ speaker: "other", text: "你報告又遲交了！" }],
      filters: { franchises: ["海綿寶寶"], categories: [], exclude_nsfw: true },
      params: {
        top_n: 3,
        candidate_k: 50,
        min_similarity: 0.35,
        diversity: 0.5,
        hotness_weight: 0.1,
      },
      client_id: "anon", // node 測試環境無 localStorage → getClientId 退回固定匿名值
      debug: true, // 後台工作台走此請求，需候選明細供 DebugPanel
    });
  });

  it("預設值與文件 04 §3 一致", () => {
    expect(DEFAULT_PARAMS).toEqual({
      top_n: 5,
      candidate_k: 50,
      min_similarity: 0.35,
      diversity: 0.5,
      hotness_weight: 0.1,
    });
    expect(DEFAULT_FILTERS.exclude_nsfw).toBe(true);
  });
});

describe("buildTaskRequest", () => {
  it("文字輸入 → input_type=text，對話放進 conversation", () => {
    const body = buildTaskRequest({ kind: "text", text: "你報告又遲交了" }, DEFAULT_FILTERS, DEFAULT_PARAMS);
    expect(body.input_type).toBe("text");
    expect(body.conversation).toEqual([{ speaker: "other", text: "你報告又遲交了" }]);
    expect(body.image).toBeNull();
    expect(body.client_id).toBe("anon");
  });

  it("截圖輸入 → input_type=screenshot，圖進 image、對話留空", () => {
    const body = buildTaskRequest({ kind: "screenshot", image: "BASE64" }, DEFAULT_FILTERS, DEFAULT_PARAMS);
    expect(body.input_type).toBe("screenshot");
    expect(body.image).toBe("BASE64");
    expect(body.conversation).toEqual([]);
  });

  it("梗圖大戰輸入 → input_type=meme_battle", () => {
    const body = buildTaskRequest({ kind: "battle", image: "BASE64" }, DEFAULT_FILTERS, DEFAULT_PARAMS);
    expect(body.input_type).toBe("meme_battle");
    expect(body.image).toBe("BASE64");
  });
});

describe("認證標頭：前台 Bearer / 後台 Basic", () => {
  // 前後台合併成同一個 SPA、同一個 origin 之後，localStorage 的前台 token 在後台頁面
  // 也讀得到。若以「有沒有 token」決定送哪一種認證，後台登入表單存進 sessionStorage
  // 的 Basic 會永遠被 Bearer 蓋掉——密碼打對也一直回「帳號或密碼錯誤」（2026-07-31）。
  const store = (initial: Record<string, string>) => {
    const map = new Map(Object.entries(initial));
    return {
      getItem: (k: string) => map.get(k) ?? null,
      setItem: (k: string, v: string) => void map.set(k, v),
      removeItem: (k: string) => void map.delete(k),
    };
  };

  const sentAuth = async (opts: {
    path: string;
    userToken?: string;
    adminCreds?: string;
  }): Promise<string | undefined> => {
    const g = globalThis as Record<string, unknown>;
    const saved = { ...g };
    g.location = { pathname: opts.path };
    g.localStorage = store(
      opts.userToken ? { "memeradar.userToken": opts.userToken } : {},
    );
    g.sessionStorage = store(
      opts.adminCreds ? { "memeradar.adminAuth": opts.adminCreds } : {},
    );
    let seen: Headers | undefined;
    g.fetch = (_url: string, init: RequestInit) => {
      seen = new Headers(init.headers as HeadersInit);
      return Promise.resolve(new Response("[]", { status: 200 }));
    };
    try {
      await apiFetch("/vlm/usage");
      return seen?.get("authorization") ?? undefined;
    } finally {
      for (const k of ["location", "localStorage", "sessionStorage", "fetch"]) {
        if (k in saved) g[k] = saved[k];
        else delete g[k];
      }
    }
  };

  it("後台頁面即使前台已登入，也要送 admin 的 Basic", async () => {
    const auth = await sentAuth({
      path: "/admin/dashboard",
      userToken: "front-desk-jwt",
      adminCreds: "YWRtaW46cHc=",
    });
    expect(auth).toBe("Basic YWRtaW46cHc=");
  });

  it("後台頁面尚未登入 → 不帶認證（讓探測拿到 401 顯示登入頁）", async () => {
    const auth = await sentAuth({ path: "/admin", userToken: "front-desk-jwt" });
    expect(auth).toBeUndefined();
  });

  it("前台頁面送使用者 Bearer，不受殘留的 admin 憑證影響", async () => {
    const auth = await sentAuth({
      path: "/",
      userToken: "front-desk-jwt",
      adminCreds: "YWRtaW46cHc=",
    });
    expect(auth).toBe("Bearer front-desk-jwt");
  });
});
