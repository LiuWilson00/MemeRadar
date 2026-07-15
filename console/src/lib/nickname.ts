import type { User } from "../types";

/** 顯示暱稱：登入者用自己設定的（沒設就生成一個存裝置上），未登入者用裝置暱稱。
 * 生成規則參考暴雪隨機 ID：形容詞 + 名詞（迷因口吻），像「臭臭束褲」「邪惡飛魚」。 */

const ADJ = [
  "臭臭", "邪惡", "惡魔", "快樂", "憂鬱", "神秘", "傳說", "隱藏", "爆走", "佛系",
  "中二", "硬派", "軟爛", "閃亮", "暴躁", "高冷", "沙雕", "廢柴", "尊爵", "頂級",
  "狂暴", "療癒", "迷幻", "電波", "微醺", "睏睏", "叛逆", "無敵", "憨憨", "焦慮",
  "躺平", "抽象", "破防", "社恐", "摸魚", "開朗", "腹黑", "呆萌", "貪吃", "懶惰",
  "資深", "骨灰級", "傲嬌", "戀愛腦", "玻璃心", "工具人", "沒睡飽", "剛下班",
];
const NOUN = [
  "束褲", "飛魚", "月亮", "貓咪", "章魚", "螃蟹", "土司", "布丁", "泡麵", "咖啡",
  "石頭", "火箭", "海豚", "企鵝", "恐龍", "河馬", "柯基", "倉鼠", "蘿蔔", "竹輪",
  "丸子", "饅頭", "蘑菇", "水母", "刺蝟", "獅子", "杯麵", "章魚燒", "皮卡丘", "柴犬",
  "貓頭鷹", "樹懶", "水獺", "熊貓", "鴨鴨", "青蛙", "番薯", "芋圓", "珍奶", "雞排",
  "滷蛋", "貢丸", "花枝", "鹹酥雞", "電風扇", "拖鞋", "仙人掌", "多肉", "吳郭魚",
];
// 偶爾補個稱號，多一分會心一笑
const TITLE = [
  "大人", "殿下", "本人", "同學", "陛下", "大師", "隊長", "部長",
  "教授", "選手", "阿北", "前輩", "3.0", "二世", "本尊",
];

function pick<T>(arr: T[]): T {
  return arr[Math.floor(Math.random() * arr.length)];
}

export function randomNickname(): string {
  return pick(ADJ) + pick(NOUN);
}

/** 梗友的隨機名字：形容詞 + 名詞，約 40% 機率再加個稱號（如「沙雕柯基大人」）。 */
export function randomBotName(): string {
  const base = pick(ADJ) + pick(NOUN);
  return Math.random() < 0.4 ? base + pick(TITLE) : base;
}

const KEY = "memeradar.nickname";

/** 裝置暱稱（首次自動生成後固定存 localStorage）。 */
export function getDeviceNickname(): string {
  if (typeof localStorage === "undefined") return randomNickname();
  let name = localStorage.getItem(KEY);
  if (!name) {
    name = randomNickname();
    localStorage.setItem(KEY, name);
  }
  return name;
}

export function setDeviceNickname(name: string): void {
  if (typeof localStorage !== "undefined") localStorage.setItem(KEY, name);
}

/** 目前該顯示的暱稱：登入且有設 → 用它；否則用裝置暱稱。 */
export function displayName(user: User | null): string {
  return user?.nickname?.trim() || getDeviceNickname();
}
