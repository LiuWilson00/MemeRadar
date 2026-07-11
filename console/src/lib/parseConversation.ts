import type { Turn } from "../types";

const PREFIXES: Array<[RegExp, Turn["speaker"]]> = [
  [/^(我|me)\s*[:：]\s*/i, "me"],
  [/^(對方|对方|other)\s*[:：]\s*/i, "other"],
];

/** 多行貼上 → 對話輪次。有「我：/對方：」前綴就用；否則交替分配且最後一句為對方。 */
export function parsePastedConversation(raw: string): Turn[] {
  const lines = raw
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  if (lines.length === 0) return [];

  const explicit = lines.map((line) => {
    for (const [pattern, speaker] of PREFIXES) {
      if (pattern.test(line)) return { speaker, text: line.replace(pattern, "").trim() };
    }
    return null;
  });
  if (explicit.every((t) => t !== null)) return explicit as Turn[];

  // 無（完整）前綴：由後往前交替，最後一句是「要回覆的對方」
  return lines.map((text, i) => ({
    speaker: (lines.length - 1 - i) % 2 === 0 ? "other" : "me",
    text,
  }));
}
