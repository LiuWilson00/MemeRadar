import { describe, expect, it } from "vitest";
import { summarize, type UploadItem } from "./uploadQueue";

let _n = 0;
const item = (status: UploadItem["status"], pendingReview = false): UploadItem => ({
  id: `i${_n++}`,
  name: "x.png",
  status,
  message: "",
  pendingReview,
});

describe("summarize", () => {
  it("依狀態計數；active = 排隊 + 處理中", () => {
    const s = summarize([
      item("done"),
      item("done", true),
      item("duplicate"),
      item("error"),
      item("queued"),
      item("uploading"),
    ]);
    expect(s).toEqual({ total: 6, done: 2, duplicate: 1, error: 1, pendingReview: 1, active: 2 });
  });

  it("空佇列全為 0", () => {
    expect(summarize([])).toEqual({
      total: 0,
      done: 0,
      duplicate: 0,
      error: 0,
      pendingReview: 0,
      active: 0,
    });
  });
});
