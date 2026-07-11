import { describe, expect, it } from "vitest";
import { parsePastedConversation } from "./parseConversation";

describe("parsePastedConversation", () => {
  it("解析明確前綴（我：/對方：，全半形冒號皆可）", () => {
    const turns = parsePastedConversation(
      "對方：你報告又遲交了！\n我: 抱歉抱歉\nother:每次都這樣",
    );
    expect(turns).toEqual([
      { speaker: "other", text: "你報告又遲交了！" },
      { speaker: "me", text: "抱歉抱歉" },
      { speaker: "other", text: "每次都這樣" },
    ]);
  });

  it("無前綴時交替分配，最後一句必為對方（要回覆的人）", () => {
    const turns = parsePastedConversation("早安\n吃飯沒\n沒有，你請嗎");
    expect(turns.map((t) => t.speaker)).toEqual(["other", "me", "other"]);
    expect(turns[2].text).toBe("沒有，你請嗎");
  });

  it("略過空行並修剪空白", () => {
    const turns = parsePastedConversation("  哈囉  \n\n\n 在嗎 ");
    expect(turns).toEqual([
      { speaker: "me", text: "哈囉" },
      { speaker: "other", text: "在嗎" },
    ]);
  });

  it("空輸入回空陣列", () => {
    expect(parsePastedConversation("   \n  ")).toEqual([]);
  });
});
