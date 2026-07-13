import { describe, expect, it } from "vitest";
import { buildRecommendRequest, buildTaskRequest, DEFAULT_FILTERS, DEFAULT_PARAMS } from "./api";

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
