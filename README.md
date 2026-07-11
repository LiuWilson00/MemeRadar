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
