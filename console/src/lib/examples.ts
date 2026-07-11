import type { Turn } from "../types";

export interface Example {
  label: string;
  turns: Turn[];
}

/** 範例對話（docs/05 §2.1：一鍵載入，Demo 免打字） */
export const EXAMPLES: Example[] = [
  {
    label: "被主管釘：報告遲交",
    turns: [
      { speaker: "other", text: "你報告又遲交了！" },
      { speaker: "me", text: "抱歉抱歉" },
      { speaker: "other", text: "每次都這樣，你到底行不行" },
    ],
  },
  {
    label: "朋友抱怨加班",
    turns: [
      { speaker: "other", text: "今天又加班到十點" },
      { speaker: "other", text: "老闆還說年輕人要多學習" },
    ],
  },
  {
    label: "朋友報喜：錄取了",
    turns: [
      { speaker: "other", text: "！！！我錄取了！！！" },
      { speaker: "other", text: "下個月開始上班" },
    ],
  },
  {
    label: "對方已讀很久才回「好」",
    turns: [{ speaker: "other", text: "好" }],
  },
  {
    label: "被虧遊戲太爛",
    turns: [
      { speaker: "other", text: "昨天那場你也太雷了吧" },
      { speaker: "other", text: "掛機都比你有用" },
    ],
  },
  {
    label: "對方提出離譜要求",
    turns: [
      { speaker: "other", text: "欸幫我做一下簡報" },
      { speaker: "me", text: "什麼時候要？" },
      { speaker: "other", text: "明天早上，五十頁，順便翻成英文" },
    ],
  },
];
