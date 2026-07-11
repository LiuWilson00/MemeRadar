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
    ReportView            回饋報表（KPI / 每日趨勢 / 分組通過率 / 👎 歸因）
```

截圖上傳：左欄「上傳對話截圖」→ VLM 解析為氣泡（約 5–8 秒）→ 人工確認左右方與內容 → 送出推薦。截圖僅在記憶體處理，不會保存。

頁籤：
- **工作台**——推薦主流程（輸入 / 結果 / 參數 / Debug）
- **查詢歷史**——歷次查詢與 👍👎 統計；「重放」載回當時輸入與參數並自動重跑
- **梗圖庫**——篩選瀏覽與標註詳情；「＋ 上傳梗圖」為 seed 匯入口（匯入 → 標註 → 向量化一條龍，約 8–12 秒後即可被檢索）
- **複核**——標註待審（低信心 / 非梗圖 / 拒答：編修標籤後通過或淘汰，通過自動重建向量）與去重裁決（並排比圖判合併或不同梗）
- **報表**——回饋報表（`GET /report/feedback`）：👍 率 KPI、每日 👍👎 趨勢長條（附數據表備援）、依策略 / 系列 / 名次 / 參數組合的通過率、👎 備註列表與五類歸因指引（意圖錯 / query 爛 / 庫缺圖 / 排序錯 / 梗過時，docs/06 §3.6）
