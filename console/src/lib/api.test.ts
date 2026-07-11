import { describe, expect, it } from "vitest";
import { buildRecommendRequest, DEFAULT_FILTERS, DEFAULT_PARAMS } from "./api";

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
