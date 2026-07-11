# MemeRadar 調適控制台

React + Vite + Tailwind 的內部 Demo / 調參工作台（規格：[docs/05](../docs/05-demo-console.md)）。

## 開發

```bash
# 1. 先啟動後端 API（repo 根目錄）
python -m memeradar.api          # http://127.0.0.1:8000

# 2. 啟動前端（本目錄）
npm install
npm run dev                      # http://localhost:5173（已代理 API）
```

## 指令

| 指令 | 用途 |
|------|------|
| `npm run dev` | 開發伺服器（`/recommend` 等路徑代理到 :8000） |
| `npm test` | lib 層單元測試（vitest） |
| `npm run build` | 型別檢查 + production 打包 |

## 結構

```
src/
  App.tsx                 三欄工作台佈局與狀態
  types.ts                API 型別（對齊 docs/01 §5.2 契約）
  lib/api.ts              API client 與預設參數（對齊 docs/04 §3）
  lib/parseConversation.ts  貼上文字 → 對話輪次
  lib/examples.ts         範例對話（一鍵載入）
  components/
    ConversationEditor    聊天氣泡編輯器（發話者切換 / 貼上解析）
    ParamsPanel           梗圖包 / 分類 / NSFW / 滑桿群 / 重跑
    ResultCard            結果卡片（分數拆解 / 策略徽章 / 👍👎+備註）
    DebugPanel            意圖 JSON / 候選池表格 / 各階段耗時
    RadarLoading          雷達掃描等待動畫 + 管線階段燈號
```

截圖上傳流程待 P2-5（後端截圖解析）完成後啟用。
