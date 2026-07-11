# MemeRadar

AI 梗圖回應推薦系統：根據對話上下文，自動精準推薦適合回覆的梗圖。

- 產品與模組規格：[docs/](docs/README.md)
- 開發任務列表：[docs/TASKS.md](docs/TASKS.md)

## 專案結構

```
memeradar/            Python 套件
  ingestion/          爬蟲與資料獲取（docs/02，Phase 3）
  understanding/      圖片理解與向量索引（docs/03，Phase 1）
  matching/           對話意圖與匹配（docs/04，Phase 2）
  shared/             設定、taxonomy、資料模型
    data/taxonomy.yaml  標籤 Taxonomy v1（標註端與意圖端唯一共同來源）
console/              Demo & Debug Console 前端（docs/05，Phase 2）
tests/                測試
docs/                 規格文件
```

## 開發環境（Python 3.11+）

```bash
# 1. 建立虛擬環境
python -m venv .venv

# 2. 啟用（Windows PowerShell）
.venv\Scripts\Activate.ps1
#    啟用（Git Bash / macOS / Linux）
source .venv/Scripts/activate   # unix 下為 .venv/bin/activate

# 3. 安裝（含開發依賴）
python -m pip install -e ".[dev]"

# 4. 設定 secrets（跑測試不需要；呼叫 Claude / Voyage API 前需要）
cp .env.example .env  # 再填入 ANTHROPIC_API_KEY 等
```

## 常用指令

有 `make` 的環境直接用 Makefile；Windows 本機無 make 時用右欄等價指令：

| make 目標 | 等價指令 |
|-----------|---------|
| `make test` | `python -m pytest` |
| `make lint` | `python -m ruff check .` |
| `make check` | 依序執行上述兩者 |
| `make install` | `python -m pip install -e ".[dev]"` |

初始化 / 升級本機資料庫（SQLite，位於 `MEMERADAR_DATA_DIR`，預設 `./data`）：

```bash
python -m memeradar.shared.db
```

匯入人工 seed 梗圖（可依主題建子資料夾，資料夾名會成為標註時的上下文提示；重跑冪等）：

```bash
python -m memeradar.ingestion.seed_import <資料夾>
```

批次標註尚未標註的梗圖（需先在 `.env` 設定 `ANTHROPIC_API_KEY`）：

```bash
python -m memeradar.understanding.annotator [--limit N]
```

批次向量化已標註的梗圖（BGE-M3 本地推論；需先 `pip install -e ".[local-embedding]"`，首次執行會自動下載約 2.3GB 模型權重）：

```bash
python -m memeradar.understanding.embedding [--limit N]
```

檢索驗證：一句話 query 查 Top-K（調校檢索品質用）：

```bash
python -m memeradar.matching.cli "被老闆罵了想擺爛" --top 10 [--franchise 海綿寶寶] [--category 卡通動畫] [--min-similarity 0.35] [--show-doc]
```

抓取 Reddit 梗圖候選（需先在 `.env` 填 `REDDIT_CLIENT_ID/SECRET`，於 reddit.com/prefs/apps 建立 script app）：

```bash
python -m memeradar.ingestion.reddit --client praw --subreddit memes --limit 25 [--update-watermark] [--json]
```

全自動資料管線（抓取 → 過濾 → 去重 → 標註 → 向量化，一鍵完成；供排程器定期觸發，如 Windows 工作排程器或 cron 每日一次）：

```bash
python -m memeradar.ingestion.pipeline --client praw [--subreddit memes] [--limit 100] [--no-clip]
```

意圖分析驗證：對話 → 意圖 JSON（需 `ANTHROPIC_API_KEY`；每個參數一則訊息）：

```bash
python -m memeradar.matching.intent "other:你報告又遲交了！" "me:抱歉抱歉" "other:你到底行不行"
```

啟動推薦 API（http://127.0.0.1:8000，互動文件在 /docs；需 `ANTHROPIC_API_KEY` 與 local-embedding extras）：

```bash
python -m memeradar.api
```

啟動調適控制台（http://localhost:5173，需先啟動 API；詳見 [console/README.md](console/README.md)）：

```bash
cd console && npm install && npm run dev
```
