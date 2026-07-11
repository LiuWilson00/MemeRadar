# 系統架構與技術選型

> 本文件描述 MemeRadar 的整體架構、資料流、模組間介面契約與技術 / 模型選型。依需求，後端框架與資料庫**不做最終定案**，以「建議候選」標示；AI 模型與演算法層則給出明確建議。

## 1. 架構總覽

系統分成兩條主線：**離線資料管線**（爬蟲 → 理解 → 索引，批次執行）與**線上推薦服務**（對話輸入 → 意圖分析 → 檢索排序 → 展示回饋，即時執行）。兩者透過「梗圖庫」（物件儲存 + 結構化 metadata + 向量索引）銜接；Console 的回饋事件回流至評估與調參。

```mermaid
flowchart TB
    subgraph OFFLINE["離線資料管線（批次）"]
        SRC["資料源<br/>Reddit / Dcard / 人工匯入"]
        CRAWL["爬蟲排程器<br/>抓圖 + 貼文 metadata"]
        DEDUP["清洗與去重<br/>SHA256 / pHash / CLIP 相似度"]
        FILTER["梗圖價值過濾<br/>規則引擎 + VLM 分類"]
        LABEL["AI 標註<br/>Claude Vision + Batch API<br/>OCR / 描述 / 情緒 / 使用情境"]
        EMBED["向量化<br/>檢索文件組裝 + Text Embedding"]
        SRC --> CRAWL --> DEDUP --> FILTER --> LABEL --> EMBED
    end

    subgraph STORE["梗圖庫"]
        OBJ[("物件儲存<br/>原始圖檔")]
        META[("Metadata DB<br/>標籤 / 來源 / 熱度")]
        VEC[("向量索引<br/>語意檢索")]
    end

    EMBED --> OBJ
    EMBED --> META
    EMBED --> VEC

    subgraph ONLINE["線上推薦服務（即時）"]
        CONSOLE["Demo Console<br/>文字輸入 / 截圖上傳 / 參數面板"]
        PARSE["輸入解析<br/>截圖 → 結構化對話（VLM）"]
        INTENT["意圖分析 LLM<br/>情緒 / Punchline / 回應策略 / 檢索 query"]
        SEARCH["多維度檢索<br/>向量搜尋 + Metadata 過濾"]
        RERANK["重排序與多樣化<br/>Rerank + MMR"]
        CONSOLE --> PARSE --> INTENT --> SEARCH --> RERANK --> CONSOLE
    end

    VEC --> SEARCH
    META --> SEARCH
    OBJ --> CONSOLE

    FEEDBACK[("回饋事件庫<br/>評分 / 備註 / 參數快照")]
    CONSOLE -- "👍 / 👎 回饋" --> FEEDBACK
    FEEDBACK -. "評估報表 / 調參 / 微調資料" .-> RERANK
```

## 2. 離線批次流程（爬取 → 入庫）

```mermaid
sequenceDiagram
    autonumber
    participant SCH as 排程器
    participant CR as 爬蟲 Worker
    participant DD as 去重服務
    participant VLM as Claude Batch API
    participant EMB as Embedding 服務
    participant DB as 梗圖庫

    SCH->>CR: 觸發（每日 / 每 6 小時，帶上次抓取水位）
    CR->>CR: 抓取新貼文（圖片 + 標題 + 熱門留言 + 互動數）
    CR->>DD: 提交候選圖片
    DD->>DD: SHA256 精確去重 → pHash 近似去重 → CLIP 語意去重
    DD-->>DB: 重複圖：合併 metadata、累加熱度計數
    DD->>VLM: 新圖批次提交（含貼文上下文）
    Note over VLM: 單次 VLM Pass 產出結構化 JSON：<br/>is_meme / OCR / 描述 / 角色 / 作品 /<br/>情緒 / 使用情境 / 分類 / NSFW / 信心度
    VLM-->>DD: 標註結果（is_meme=false 者淘汰）
    VLM->>EMB: 合格梗圖的標註結果
    EMB->>EMB: 組裝檢索文件 → 產生 text embedding
    EMB->>DB: 寫入圖檔 / metadata / 向量
    DB-->>SCH: 回報本批統計（新增 / 去重 / 淘汰 / 低信心待審）
```

## 3. 線上推薦流程（對話 → Top 5）

```mermaid
sequenceDiagram
    autonumber
    participant U as 測試者
    participant C as Demo Console
    participant API as 推薦 API
    participant V as 截圖解析 VLM
    participant I as 意圖分析 LLM
    participant S as 向量檢索
    participant R as Rerank

    U->>C: 貼上對話文字 或 上傳截圖 + 設定過濾條件
    C->>API: POST /recommend
    alt 輸入為截圖
        API->>V: 解析截圖（氣泡左右 → 發話者 / 順序 / 文字）
        V-->>API: 結構化對話 JSON（Console 可供人工修正）
    end
    API->>I: 對話歷史 + 過濾條件
    I-->>API: 意圖分析 JSON（情緒 / Punchline / N 個回應策略與檢索 query）
    par 每個回應策略平行檢索
        API->>S: query embedding + metadata filter（梗圖包 / 分類 / NSFW）
        S-->>API: 候選池 Top-K（K≈50）
    end
    API->>R: 候選池 + 對話上下文
    R-->>API: 重排序分數 + MMR 多樣化 → Top 3–5
    API-->>C: 結果（圖 + 推薦理由 + 分數拆解 + debug 中間產物）
    U->>C: 👍 / 👎 + 備註
    C->>API: POST /feedback（含參數快照）
```

## 4. 資料模型（概念層）

> 僅描述實體與關聯，不綁定特定資料庫。

```mermaid
erDiagram
    MEME ||--o{ MEME_SOURCE : "出現於"
    MEME ||--|| MEME_ANNOTATION : "擁有"
    MEME ||--o{ EMBEDDING : "擁有"
    MEME }o--o| TEMPLATE : "衍生自"
    RECOMMENDATION_LOG ||--o{ FEEDBACK_EVENT : "收到"
    MEME ||--o{ FEEDBACK_EVENT : "被評"

    MEME {
        string meme_id PK
        string image_uri "物件儲存位置"
        string sha256 "精確去重鍵"
        string phash "感知雜湊"
        int width_height
        float hotness "熱度分數（重複出現 + 互動數 + 時間衰減）"
        string status "active / pending_review / removed"
        datetime first_seen_at
    }
    MEME_SOURCE {
        string source_id PK
        string meme_id FK
        string platform "reddit / dcard / manual"
        string post_url
        string post_title
        json top_comments
        int upvotes
        datetime posted_at
    }
    MEME_ANNOTATION {
        string meme_id FK
        string ocr_text "圖中文字"
        string description "視覺情境描述"
        json characters "主體角色，如海綿寶寶"
        string franchise "作品來源，如甄嬛傳"
        json emotions "情緒標籤（固定字典）"
        json usage_hints "使用情境（檢索核心欄位）"
        json categories "分類目錄：動漫 / 美劇 / 政治…"
        bool nsfw
        float confidence
        string model_version "標註模型與 prompt 版本"
    }
    EMBEDDING {
        string meme_id FK
        string kind "text_retrieval / image_dedup"
        string model "embedding 模型與版本"
        vector vector
    }
    TEMPLATE {
        string template_id PK
        string name "如：派大星攤手"
    }
    RECOMMENDATION_LOG {
        string query_id PK
        json conversation "輸入對話（結構化）"
        json intent_result "意圖分析完整 JSON"
        json params_snapshot "top_k / threshold / 過濾條件 / 模型版本"
        json candidates "候選池與各階段分數"
        json final_results "Top 3-5 與推薦理由"
        int latency_ms
        datetime created_at
    }
    FEEDBACK_EVENT {
        string feedback_id PK
        string query_id FK
        string meme_id FK
        int rank "該圖在結果中的名次"
        string rating "up / down"
        string note "測試者備註"
        datetime created_at
    }
```

## 5. 模組間介面契約（概念版）

### 5.1 標註輸出（Understanding 模組 → 梗圖庫）

```jsonc
{
  "meme_id": "m_01H...",
  "is_meme": true,
  "ocr_text": "我就爛",
  "description": "海綿寶寶攤手站立，表情理直氣壯，配上大字「我就爛」",
  "characters": ["海綿寶寶"],
  "franchise": "海綿寶寶",
  "emotions": ["擺爛", "理直氣壯"],
  "usage_hints": [
    "被指責能力不足或偷懶時，理直氣壯地自嘲認了",
    "拒絕改進、表達躺平態度"
  ],
  "categories": ["卡通動畫"],
  "nsfw": false,
  "confidence": 0.93,
  "model_version": "labeler-v1@claude-opus-4-8"
}
```

### 5.2 推薦 API（Console → 線上服務）

```jsonc
// POST /recommend
{
  "input_type": "text",                    // text | screenshot
  "conversation": [                         // input_type=text 時提供
    {"speaker": "other", "text": "你報告又遲交了！"},
    {"speaker": "me",    "text": "抱歉抱歉"},
    {"speaker": "other", "text": "每次都這樣，你到底行不行"}
  ],
  "image": "<base64>",                     // input_type=screenshot 時提供
  "filters": {
    "franchises": ["海綿寶寶", "甄嬛傳"],  // 空陣列 = 不限
    "categories": [],
    "exclude_nsfw": true
  },
  "params": {
    "top_n": 5,                             // 回傳張數 3–5
    "candidate_k": 50,                      // 向量檢索候選池大小
    "min_similarity": 0.35,                 // 相似度下限
    "diversity": 0.5                        // MMR λ，0=只看相關性 1=最大化多樣性
  }
}

// Response
{
  "query_id": "q_01H...",
  "intent": { "...": "意圖分析完整 JSON，見 04 文件" },
  "results": [
    {
      "meme_id": "m_01H...",
      "image_url": "...",
      "rank": 1,
      "scores": {"vector": 0.82, "rerank": 0.91, "final": 0.89},
      "matched_strategy": "滑跪求饒",
      "matched_tags": ["滑跪", "求饒", "認錯"],
      "reason": "對方處於憤怒且重複指責的情境，此圖的使用情境「犯錯被抓包時誇張下跪求饒」與所選回應策略高度吻合"
    }
  ],
  "debug": { "queries": ["..."], "candidates": ["..."], "timings_ms": {"...": 0} }
}
```

## 6. 技術與模型選型

### 6.1 AI 模型（明確建議）

| 用途 | 建議 | 理由與備註 |
|------|------|-----------|
| 圖片標註（OCR + 描述 + 標籤，單一 VLM pass） | **Claude `claude-opus-4-8`** via Messages API + **structured outputs**（`output_config.format` 保證合法 JSON） | 高解析視覺（長邊 2576px）、繁中 OCR 與網路文化理解俱佳；一次 pass 同時完成 OCR / 描述 / 情緒 / 情境，免去自建 OCR pipeline。批次標註走 **Batch API（費用 −50%）**，共用 system prompt 搭配 **prompt caching** |
| 標註成本降級選項 | `claude-sonnet-5` 或 `claude-haiku-4-5` 做第一層「是否梗圖 / NSFW」粗篩 | 是否採用由團隊依成本評估決定；預設全程 opus。粗篩淘汰非梗圖後，昂貴的完整標註只跑合格圖 |
| 截圖解析（對話結構還原） | Claude `claude-opus-4-8`（vision + structured outputs） | 需辨識氣泡左右方（自己 / 對方）、順序、時間戳，是結構化理解不只是 OCR |
| 意圖分析（線上、即時） | Claude `claude-opus-4-8` + structured outputs | 品質敏感路徑；輸出多策略檢索 query。延遲若成瓶頸再評估降級 |
| Rerank | 首選 **LLM listwise rerank**（Claude 對候選 20–30 張的標註摘要打分）；備選 Voyage `rerank` 系列模型 | LLM rerank 可同時產出「推薦理由」文字，一石二鳥 |
| Text Embedding（檢索主軸） | 候選一：**Voyage AI**（Anthropic 官方推薦的 embedding 夥伴，多語系佳）；候選二：**BGE-M3**（開源自架、中文強、零 API 成本） | 以介面封裝（`embed(texts) -> vectors`）便於 A/B 切換；embedding 模型版本必須記錄在向量 metadata，換模型 = 全量重建索引 |
| Image Embedding（去重 / 以圖搜圖） | 開源 **CLIP / SigLIP**（自架推論即可） | 僅用於去重與相似圖聚合，非檢索主軸 |
| OCR 輔助驗證（可選） | PaddleOCR（開源，繁中佳） | 僅在 VLM OCR 抽樣品質不達標時引入交叉驗證，預設不用 |

### 6.2 基礎設施（候選建議，暫不定案）

| 層 | 候選 | 備註 |
|----|------|------|
| 主要語言 | Python 3.11+ | AI / 爬蟲生態最完整 |
| 爬蟲框架 | `httpx` + `PRAW`（Reddit 官方 API）+ `Playwright`（動態頁面） | 見 02 文件 |
| 批次排程 | 先 cron / APScheduler，量大再上 Prefect | Demo 階段不需要重型 workflow 引擎 |
| 後端 API | FastAPI（候選） | async、pydantic schema 與本設計高度契合 |
| Metadata DB + 向量索引 | 候選：PostgreSQL + pgvector（單庫搞定 metadata filter + 向量）；或 Qdrant（過濾語法與量級擴展較佳） | **暫不定案**；Demo 量級（<10 萬張）兩者皆遠遠夠用 |
| 物件儲存 | 本機磁碟 →（未來）S3 相容儲存 | Demo 階段本機即可 |
| 前端 Console | React + Vite + Tailwind（候選）；最速備選 Streamlit | 見 05 文件的取捨分析 |

### 6.3 選型原則

1. **可替換性**：embedding、向量庫、rerank 皆以 thin interface 封裝，任何一項都能在一天內換掉重測。
2. **版本可追溯**：標註 prompt、模型 ID、embedding 模型全部寫入資料列（`model_version`），否則日後無法做一致性評估與增量重標。
3. **成本意識但不過早優化**：Demo 階段以品質為先（預設 opus），等 golden set 建立後再用數據決定哪些環節可降級。
