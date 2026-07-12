import { afterEach, describe, expect, it, vi } from "vitest";
import { uploadMemeClassified } from "./api";

function mockFetch(status: number, body: unknown) {
  return vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    statusText: "",
    json: async () => body,
  } as Response);
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("uploadMemeClassified", () => {
  it("200 → done，帶出 meme_id / OCR / 是否轉複核", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch(200, {
        meme_id: "m1",
        meme_status: "active",
        annotation: { ocr_text: "我就爛" },
      }),
    );

    const outcome = await uploadMemeClassified("BASE64", "海綿寶寶");

    expect(outcome).toEqual({ kind: "done", memeId: "m1", pendingReview: false, ocr: "我就爛" });
  });

  it("meme_status=pending_review → pendingReview 為真", async () => {
    vi.stubGlobal(
      "fetch",
      mockFetch(200, { meme_id: "m2", meme_status: "pending_review", annotation: null }),
    );

    const outcome = await uploadMemeClassified("BASE64", "");

    expect(outcome).toMatchObject({ kind: "done", pendingReview: true, ocr: "" });
  });

  it("409 → duplicate", async () => {
    vi.stubGlobal("fetch", mockFetch(409, { detail: "圖片已存在（m9）" }));

    const outcome = await uploadMemeClassified("BASE64", "");

    expect(outcome).toEqual({ kind: "duplicate", message: "圖片已存在（m9）" });
  });

  it("422 → error 帶後端訊息", async () => {
    vi.stubGlobal("fetch", mockFetch(422, { detail: "無法讀取圖片（僅支援 PNG / JPEG / WebP）" }));

    const outcome = await uploadMemeClassified("BASE64", "");

    expect(outcome).toEqual({
      kind: "error",
      message: "無法讀取圖片（僅支援 PNG / JPEG / WebP）",
    });
  });

  it("網路例外 → error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("Failed to fetch")));

    const outcome = await uploadMemeClassified("BASE64", "");

    expect(outcome).toEqual({ kind: "error", message: "Failed to fetch" });
  });
});
