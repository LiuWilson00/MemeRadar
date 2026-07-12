import { describe, expect, it } from "vitest";
import { imageFilesFrom } from "./files";

const f = (name: string, type: string) => ({ name, type }) as unknown as File;

describe("imageFilesFrom", () => {
  it("只保留 PNG / JPEG / WebP，濾掉其他型別與非檔案", () => {
    const kept = imageFilesFrom([
      f("a.png", "image/png"),
      f("b.jpg", "image/jpeg"),
      f("c.webp", "image/webp"),
      f("d.gif", "image/gif"),
      f("notes.txt", "text/plain"),
    ]);
    expect(kept.map((x) => x.name)).toEqual(["a.png", "b.jpg", "c.webp"]);
  });

  it("null / undefined 視為空清單", () => {
    expect(imageFilesFrom(null)).toEqual([]);
    expect(imageFilesFrom(undefined)).toEqual([]);
  });
});
