# MemeRadar — Zeabur 生產部署設計（Infra 文件）

> 目標讀者：部署／維運本專案的人。內容涵蓋 (1) 架構與服務清單、(2) 每個服務怎麼設定、
> (3) 上線前必改的程式碼、(4) 部署步驟、(5) 驗收、(6) 成本與擴充路徑。
> Zeabur 事實均取自官方文件（見各段來源），2026-07 查證。

---

## 0. TL;DR

三個 Zeabur 服務 + 一份 Dockerfile：

| 服務 | 類型 | 用途 |
|---|---|---|
| `postgres` | Prebuilt Docker（**pgvector 模板** `pgvector/pgvector:pg18`）+ Volume | 結構化資料 + 向量檢索 |
| `api` | Git（**自訂 Dockerfile**，Root Dir = `/`）+ Volume | FastAPI + BGE-M3 + NVIDIA 呼叫 |
| `frontend` | Git（Vite 靜態，Zeabur 自動 build，Root Dir = `console/`） | 前台手機 client + 後台 Console |

- DB 連線走**私有網路** `postgresql.zeabur.internal`，用 `${POSTGRES_*}` 參考變數注入 `DATABASE_URL`。
- migration 走 **start command**：`alembic upgrade head && …`（基準版會自動 `CREATE EXTENSION vector`）。
- 上傳圖片存 **Volume**（換取 Recreate 部署策略＝重部署時短暫停機；規模化再改物件儲存）。
- 生產請用 **Pro 方案**（Free 會 auto-sleep 冷啟、且無自動備份）。

---

## 1. 架構

```
   使用者(手機/桌機)
        │  HTTPS
        ▼
┌──────────────────┐        ┌───────────────────────────┐        ┌────────────────────┐
│ frontend         │  CORS  │ api                       │  私有   │ postgres           │
│ Vite 靜態 / Caddy │──────▶ │ FastAPI + BGE-M3 (in-proc)│  網路   │ pgvector/pgvector  │
│ 前台 / 後台       │  fetch │  意圖/rerank/截圖 → NVIDIA │◀──────▶│  :pg18             │
│ *.zeabur.app     │        │  pgvector 檢索             │ 5432   │  Volume: PG 資料   │
└──────────────────┘        │  Volume: 上傳圖片          │        └────────────────────┘
                            │ *.zeabur.app              │
                            └────────────┬──────────────┘
                                         │ HTTPS
                                         ▼
                                 NVIDIA NIM（外部免費 VLM/LLM API）
```

**為什麼前後端拆開（而非 API 直接吐前端）**：前台手機 client 是公開、要「隨時可用、秒開」的；
API 很重（torch + BGE ~2–4GB RAM、冷啟 ~40s，且掛 Volume 後每次重部署會短暫停機）。拆開後，
靜態前台由 Caddy 服務、永遠在線、與重量級 API 的可用性解耦。代價是要處理 **CORS** 與**前端 API base URL**
（見 §3 程式碼修改）。若你偏好最少改動、可接受耦合，另有「單體」變體見 §7。

---

## 2. Zeabur 服務設定

### 2.1 `postgres`（PostgreSQL + pgvector）

- 建立方式：Add Service →（A）用 **pgvector 模板**（`zeabur.com/templates/773OAW`，部署 `pgvector/pgvector:pg18`），
  或（B）Deploy Docker Image → `pgvector/pgvector:pg18`。兩者 pgvector binaries 皆已內建。
- **Volume**：Volumes 分頁 → Mount，`Mount Directory = /var/lib/postgresql/data`（PG 資料落地）。
  ⚠️ 首次掛載會清空該目錄；掛 Volume 後此服務改為 Recreate 策略。
- **不需**手動 `CREATE EXTENSION vector`：api 服務啟動時 `alembic upgrade head` 的基準版第一條 DDL
  就是 `CREATE EXTENSION IF NOT EXISTS vector`（前提是用 pgvector 映像，binaries 已在）。
- 暴露的參考變數（同專案內其他服務可用）：`${POSTGRES_CONNECTION_STRING}`、`${POSTGRES_HOST}`、
  `${POSTGRES_PORT}`、`${POSTGRES_USERNAME}`、`${POSTGRES_PASSWORD}`、`${POSTGRES_DATABASE}`。
  > 註：自訂 Docker 服務暴露的變數名可能與 marketplace 版略有出入；必要時自行組 DSN：
  > `postgresql://root:${PASSWORD}@postgresql.zeabur.internal:5432/postgres`。

來源：`zeabur.com/docs/en-US/marketplace/postgresql`、`.../deploy/customize-prebuilt`、`.../deploy/networking/private-networking`

### 2.2 `api`（FastAPI + BGE-M3）

- 建立方式：Add Service → Git → 選本 repo，**Root Directory = `/`**（monorepo 根）。
- 用 **自訂 Dockerfile**（見 §5）。理由：本 app 進入點是套件（`memeradar.api`）、又含 torch/BGE 重依賴，
  用 Dockerfile 對建置與啟動指令最可控；zbpack 自動偵測未必抓得到 `memeradar.api`。
  - monorepo Dockerfile 命名：`api.Dockerfile` 或 `Dockerfile.api`（Zeabur 依服務名匹配）。
- **Volume**：`Mount Directory = /data`（＝ `MEMERADAR_DATA_DIR`，上傳圖片落地在 `/data/images`）。
- **Health Check**：Settings → Health Check → Path = `/health`（回 2xx 才算 ready）。
- **Port**：容器須聽 `$PORT`（Zeabur 注入；Git 服務未設時預設 8080）。見 §3 host binding 修改。
- migration：走 start command（Dockerfile CMD 內 `alembic upgrade head && uvicorn …`）。

來源：`.../guides/python`、`.../deploy/config/root-directory`、`.../operations/monitoring/health-checks`、`.../networking/public`

### 2.3 `frontend`（Vite 靜態站）

- 建立方式：Add Service → Git → **同一個 repo**，**Root Directory = `console/`**。
- Zeabur 偵測到 Node/Vite → **自動 `npm run build`**、以 Caddy 靜態服務。
- 設定（Variables 分頁）：
  - `ZBPACK_OUTPUT_DIR=dist`（build 產物目錄）
  - `VITE_API_BASE_URL=https://<api 的網域>.zeabur.app`（**build 期**注入；見 §3 前端修改）
- **SPA fallback 自動**：找不到檔案時回 `index.html`（前台 `/`）。後台在 `/admin.html`（多入口已在 vite build 設定）。

來源：`.../guides/static`、`.../guides/nodejs/vite`、`.../guides/nodejs`

---

## 3. 上線前必改的程式碼（Checklist）

> 這些是「不改就會壞 / 不安全」的項目。每項附最小 snippet。可請我直接實作。

### 🔴 3.1 uvicorn 綁 `0.0.0.0` + 讀 `$PORT`（否則容器外打不到）
`memeradar/api/app.py` 的 `main()`：
```python
def main() -> None:
    import os
    import uvicorn
    uvicorn.run(create_app(), host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
```
（若走 Dockerfile CMD 直接 `uvicorn --factory`，此處可不動，但仍建議修正。）

### 🔴 3.2 CORS（前後端不同網域）
`create_app()` 內、其他 middleware 之前：
```python
from fastapi.middleware.cors import CORSMiddleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o for o in os.environ.get("CORS_ORIGINS", "").split(",") if o],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```
`api` 服務設 `CORS_ORIGINS=https://<frontend 網域>.zeabur.app`（多個逗號分隔）。

### 🔴 3.3 前端可設定的 API base（目前全是相對路徑）
`console/src/lib/api.ts` 頂部加一個 helper，所有 `fetch("/x")` 改成 `fetch(apiUrl("/x"))`：
```ts
const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";  // dev 為空 → 走 vite proxy
export const apiUrl = (path: string) => API_BASE + path;
```
dev 不填 `VITE_API_BASE_URL` → 空字串 → 相對路徑走 vite proxy（不變）；prod 填 API 網域。

### 🔴 3.4 上傳圖片：確認落在 Volume 目錄
`MEMERADAR_DATA_DIR=/data`（api 服務 env）＋ Volume 掛 `/data`。程式已用 `data_dir/images`（`seed_import`、
`meme_image` 端點），故只要 env + Volume 對上即可。⚠️ 不掛 Volume＝重部署後上傳圖片全失。

### 🟡 3.5 非同步任務孤兒回收（server 重啟時 running 任務會永遠卡住）
`create_app()` 啟動時，把殘留 running/pending 標成 error（背景 ThreadPool 不跨重啟）：
```python
# 啟動清理：上次程序沒跑完的任務標記失敗，避免前台永遠輪詢 running
startup_conn = connect(deps.db_path)
migrate(startup_conn)
startup_conn.execute(
    "UPDATE tasks SET status='error', error='服務重啟，任務中斷', updated_at=%s "
    "WHERE status IN ('pending','running')", (_now_iso(),))
startup_conn.commit()
startup_conn.close()
```

### 🟡 3.6 後台登入畫面（跨源 Basic Auth 用 fetch 不會彈原生框）
API 端 Basic Auth 已做好、也測過（帳密對才放行）。缺的是後台 SPA 一個登入頁：輸入帳密 → 存 sessionStorage →
之後每個 API 呼叫帶 `Authorization: Basic …`。跨源部署下這是必要的（否則後台 fetch 收 401 但不會有登入 UI）。
→ 可請我實作。設 `api` 服務 `ADMIN_USERNAME` / `ADMIN_PASSWORD` 才會啟用閘門。

### 🟡 3.7 公開端點限流
`/recommend`、`/tasks` 公開且每次燒 NVIDIA。建議加基本限流（依 `client_id` / IP），例如
`slowapi` 或自製 in-memory sliding window。→ 可請我實作。

### 🟢 3.8 連線池（量大再做）
目前每 request 一條 psycopg 連線。流量上來會撞 Postgres `max_connections`。可導入 `psycopg_pool.ConnectionPool`。
本規模可先不做。

---

## 4. 環境變數（每服務）

### `postgres`
| 變數 | 值 | 備註 |
|---|---|---|
| `POSTGRES_PASSWORD` | （Zeabur 自動產生 `${PASSWORD}`） | 模板已處理 |

### `api`
| 變數 | 值 | 備註 |
|---|---|---|
| `DATABASE_URL` | `postgresql://${POSTGRES_USERNAME}:${POSTGRES_PASSWORD}@postgresql.zeabur.internal:5432/${POSTGRES_DATABASE}` | 私有網路；用參考變數組 |
| `MEMERADAR_DATA_DIR` | `/data` | 對上 Volume 掛載點 |
| `NVIDIA_API_KEYS` | `nvapi-…,nvapi-…` | 多把逗號分隔 |
| `NVIDIA_VLM_MODEL` | `qwen/qwen3.5-122b-a10b` | 預設模型 |
| `ANTHROPIC_API_KEY` | （選填，目前推薦路徑全 NVIDIA） | 未用可留空 |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | 自訂 | **兩者皆填**才啟用後台登入 |
| `CORS_ORIGINS` | `https://<frontend>.zeabur.app` | 見 §3.2 |
| `PORT` | （Zeabur 注入，勿手設） | 容器須聽這個 |

### `frontend`
| 變數 | 值 | 備註 |
|---|---|---|
| `ZBPACK_OUTPUT_DIR` | `dist` | build 產物 |
| `VITE_API_BASE_URL` | `https://<api>.zeabur.app` | **build 期**注入（見 §3.3） |

> 秘密（NVIDIA keys、admin 帳密）只放 Zeabur Variables，**不進 repo**（`.env` 已 gitignore）。

---

## 5. `api` 服務的 Dockerfile

放在 repo 根，命名 `api.Dockerfile`（或 `Dockerfile.api`）：

```dockerfile
FROM python:3.12-slim

# psycopg / pillow / torch 的系統依賴
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 先裝 CPU 版 torch（避免拉進數 GB 的 CUDA），再裝專案
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

COPY pyproject.toml README.md ./
COPY memeradar ./memeradar
COPY alembic ./alembic
COPY alembic.ini ./
RUN pip install --no-cache-dir -e ".[local-embedding]"

# 把 BGE-M3 權重烘進映像（省掉 ~2.3GB runtime 下載、冷啟更快、不依賴 runtime 外網）
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-m3')"

ENV MEMERADAR_DATA_DIR=/data

# 啟動：先跑 migration（含建 extension），再起 uvicorn 綁 0.0.0.0:$PORT
CMD alembic upgrade head && \
    uvicorn "memeradar.api.app:create_app" --factory --host 0.0.0.0 --port ${PORT:-8080}
```

備註：
- 映像偏大（torch + BGE ~4–5GB）——in-process 本地免費 embedding 的必然代價。若要瘦身/加速冷啟，
  未來可把 embedding 拆成獨立服務或改用 hosted embedding API（但會失去「本地免費」）。
- `--factory` 讓 uvicorn 呼叫 `create_app()` 工廠。啟動時會暖機 BGE（~40s）。
- 單一 replica：BGE 在 process 內、任務用 in-process ThreadPool → **勿開多 replica**（見 §6）。

---

## 6. 部署步驟（Runbook）

1. **先在本機把 §3 的程式碼改好**（host binding / CORS / 前端 apiUrl / 孤兒回收；登入頁與限流可後補），
   commit、push。放好 `api.Dockerfile`。
2. Zeabur → 建立 **Project**（選離使用者近的 region）。
3. Add Service → **postgres**（pgvector 模板 `773OAW`）→ 掛 Volume 於 `/var/lib/postgresql/data`。
4. Add Service → **api**（Git，Root Dir `/`，用 `api.Dockerfile`）：
   - Variables 依 §4 填（`DATABASE_URL` 用 `${POSTGRES_*}` 參考變數）。
   - 掛 Volume 於 `/data`。
   - Settings → Health Check Path = `/health`。
   - Networking → Generate Domain（拿 `*.zeabur.app`）。
5. Add Service → **frontend**（Git，**同 repo**，Root Dir `console/`）：
   - Variables：`ZBPACK_OUTPUT_DIR=dist`、`VITE_API_BASE_URL=<api 網域>`。
   - Generate Domain。
6. 回 **api** 服務把 `CORS_ORIGINS` 設成 frontend 網域，重部署 api。
7. **首次資料搬遷**（把本機的 369 張梗圖 + 圖檔帶上去）——見 §6.1。
8. 開 frontend 網域驗收（§8）。

### 6.1 首次把既有資料帶上生產

`alembic upgrade head` 只建 schema，不含資料。既有資料兩塊：**DB 列**與**圖檔**。

- **DB**：本機已有 `scripts/migrate_sqlite_to_pg.py`（SQLite→PG）。最簡：把本機 dev PG（已含 369 張）
  用 `pg_dump` 匯出、對生產 PG（用其 public 連線字串）`pg_restore`／`psql` 匯入。或本機設
  `DATABASE_URL=<生產 PG public 字串>` 後跑一次搬遷腳本。
- **圖檔**：`data/images/`（375 檔、~24MB）需上傳到 api 服務的 `/data/images` Volume。
  可用 Zeabur CLI／容器內指令上傳，或改走物件儲存（§9）。
  ⚠️ Volume 首次掛載會清空，先確認匯入順序。

> 也可以不搬——生產從空庫開始，用後台重新上傳。但你已標註/向量化過的 369 張，搬過去省時間省 API 花費。

---

## 7. 變體：單體（1 個 API 服務同時吐前端）

若想**零前端程式碼改動、免 CORS**：讓 FastAPI 直接服務 build 好的前端（`StaticFiles`）。
- 多階段 Dockerfile：stage1 `node` build `console/dist`，stage2 python 複製 `dist` 並 `app.mount("/", StaticFiles(directory="dist", html=True))`。
- 相對路徑 `fetch("/recommend")` 因同源直接可用；後台 `/admin.html` 為 top-level 導覽 → 若加 Basic Auth 閘門會**彈瀏覽器原生登入框**（省掉 SPA 登入頁）。
- 代價：前台可用性與重量級 API 耦合、映像更大、部署耦合。**規模小、想最快上線可選這個。**

---

## 8. 部署後驗收

```bash
# API 健康 + 資料
curl https://<api>.zeabur.app/health           # {"status":"ok"}
curl https://<api>.zeabur.app/meta             # franchises 應有數字（資料有搬的話）
# 前端
open https://<frontend>.zeabur.app             # 前台載入
open https://<frontend>.zeabur.app/admin.html  # 後台（設了帳密應要登入）
```
- 送一筆推薦（前台打字），觀察是否走非同步任務、歷史頁最後出現結果（NVIDIA 免費層會慢）。
- 後台「設定」→ NVIDIA 用量表應累積呼叫紀錄。

---

## 9. 生產注意 / 擴充路徑

- **成本**：usage-based（compute + 記憶體 + Volume）。api 因 BGE 常駐吃 RAM，是主要成本項。Pro 方案起。
- **Free 方案不適合**：auto-sleep（冷啟）、無自動備份。至少 **Pro**。
- **DB 備份**：Pro 以上才有自動備份；務必開啟／定期 `pg_dump`。
- **停機視窗**：api 掛 Volume＝Recreate 策略，重部署有短暫停機。要零停機 → 把上傳圖片移到**物件儲存**
  （Cloudflare R2 / S3；需改 `seed_import` 寫入與 `meme_image` 讀取），api 就能拿掉 Volume、恢復零停機滾動部署。
- **水平擴展**：目前**單 replica** 限制（in-process BGE + in-process 任務池）。要多 replica 得先：
  (a) 任務佇列外部化（如 Redis + worker），(b) embedding 拆獨立服務或改 hosted API。
- **pgvector 索引**：目前 `vector` 欄不固定維度、無 HNSW（本規模夠快）。上量後開一支 Alembic migration：
  `ALTER TABLE embeddings ALTER COLUMN vector TYPE vector(1024)` + `CREATE INDEX … USING hnsw (vector vector_cosine_ops)`。
- **限流 / 濫用**：見 §3.7，上線前補基本限流。
- **可觀測性**：目前 uvicorn 預設 log。生產建議結構化 log + 錯誤追蹤（Sentry 等）。

---

## 附錄：config-as-code

- **`zbpack.json`**（每服務 build/runtime）：如 api 不用 Dockerfile，可用
  `{ "python": { "entry": "memeradar/api/app.py" }, "start_command": "alembic upgrade head && _startup" }`
  （自訂 start command **必須**接 `_startup`）。frontend 用 `{ "output_dir": "dist" }`。
- **Zeabur Template（YAML）**：可把整個專案（3 服務 + Volume + 變數）宣告成一份 template 一鍵部署；
  欄位以 `zeabur.com/docs/en-US/template/template-format` 為準（版本間欄位拼法略有差異，提交前對照官方頁）。
