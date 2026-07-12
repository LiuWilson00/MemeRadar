import { describe, expect, it, vi } from "vitest";
import { runUploadQueue, type UploadOutcome } from "./uploadQueue";

const file = (name: string) => ({ name }) as unknown as File;

const done = (ocr = "", pendingReview = false): UploadOutcome => ({
  kind: "done",
  memeId: "m1",
  pendingReview,
  ocr,
});

describe("runUploadQueue", () => {
  it("逐張處理並回報最終統計", async () => {
    const uploadOne = vi
      .fn<(f: File) => Promise<UploadOutcome>>()
      .mockResolvedValueOnce(done("我就爛"))
      .mockResolvedValueOnce({ kind: "duplicate", message: "圖片已存在" })
      .mockResolvedValueOnce({ kind: "error", message: "無法讀取圖片" });

    const summary = await runUploadQueue(
      [file("a.png"), file("b.png"), file("c.png")],
      uploadOne,
      () => {},
    );

    expect(summary).toEqual({ done: 1, duplicate: 1, error: 1, pendingReview: 0 });
  });

  it("單張失敗不中斷整批", async () => {
    const uploadOne = vi
      .fn<(f: File) => Promise<UploadOutcome>>()
      .mockRejectedValueOnce(new Error("網路中斷"))
      .mockResolvedValueOnce(done("附和"));

    const summary = await runUploadQueue([file("a.png"), file("b.png")], uploadOne, () => {});

    expect(uploadOne).toHaveBeenCalledTimes(2); // 第一張丟例外，第二張仍執行
    expect(summary).toEqual({ done: 1, duplicate: 0, error: 1, pendingReview: 0 });
  });

  it("低信心標註計入 pendingReview", async () => {
    const uploadOne = vi
      .fn<(f: File) => Promise<UploadOutcome>>()
      .mockResolvedValue(done("模糊", true));

    const summary = await runUploadQueue([file("a.png")], uploadOne, () => {});

    expect(summary.done).toBe(1);
    expect(summary.pendingReview).toBe(1);
  });

  it("循序執行：前一張未結束不會開始下一張", async () => {
    const orderAtCall: string[][] = [];
    let resolveFirst!: (o: UploadOutcome) => void;
    const uploadOne = vi.fn((f: File) => {
      // 呼叫當下，快照其他項的狀態，驗證循序
      orderAtCall.push(lastItems.map((i) => `${i.name}:${i.status}`));
      if (f.name === "a.png") {
        return new Promise<UploadOutcome>((r) => {
          resolveFirst = r;
        });
      }
      return Promise.resolve(done());
    });

    let lastItems: { name: string; status: string }[] = [];
    const pending = runUploadQueue([file("a.png"), file("b.png")], uploadOne, (items) => {
      lastItems = items.map((i) => ({ name: i.name, status: i.status }));
    });

    // 讓事件迴圈跑一圈；b 不該在 a resolve 前被呼叫
    await Promise.resolve();
    expect(uploadOne).toHaveBeenCalledTimes(1);

    resolveFirst(done());
    await pending;
    expect(uploadOne).toHaveBeenCalledTimes(2);
    // b 被呼叫時，a 已是終態
    expect(orderAtCall[1]).toContain("a.png:done");
  });

  it("每張都會先回報 uploading 再回報終態", async () => {
    const seen: string[] = [];
    const uploadOne = vi.fn(() => Promise.resolve(done("嗆爆")));

    await runUploadQueue([file("a.png")], uploadOne, (items) => {
      seen.push(items[0].status);
    });

    expect(seen).toContain("uploading");
    expect(seen[seen.length - 1]).toBe("done");
  });
});
