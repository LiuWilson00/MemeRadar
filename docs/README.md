# MemeRadar 規格文件目錄

| 文件 | 內容 |
|------|------|
| [00-overview.md](00-overview.md) | 產品總覽：定位、使用情境、設計原則、名詞定義、MVP 範圍、成功指標 |
| [01-architecture.md](01-architecture.md) | 系統架構（Mermaid：總覽 / 離線批次 / 線上推薦 / 資料模型）、模組介面契約、技術與模型選型 |
| [02-data-ingestion.md](02-data-ingestion.md) | 爬蟲與資料獲取模組：資料源、metadata、三層去重、價值過濾、排程 |
| [03-meme-understanding.md](03-meme-understanding.md) | 圖片理解與向量索引模組：VLM 標註管線、標籤 taxonomy、embedding 與索引、品質控管 |
| [04-intent-matching.md](04-intent-matching.md) | 對話意圖與匹配模組：截圖解析、意圖分析、回應策略、多路檢索、rerank、安全策略 |
| [05-demo-console.md](05-demo-console.md) | 展示與調適控制台：工作台 UI、參數面板、Debug 面板、回饋迴圈 |
| [06-risks-and-challenges.md](06-risks-and-challenges.md) | 合規風險、內容安全、技術挑戰（時效性 / 冷啟動 / 同模板不同字）、開放問題 |
| [TASKS.md](TASKS.md) | 開發任務列表：Phase 0–4、相依關係、驗收標準、決策點對照 |

**建議閱讀順序**：00 → 01 → （依興趣挑模組 02–05）→ 06 → TASKS。
