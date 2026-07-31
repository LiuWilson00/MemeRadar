"""設定與 secrets 管理。

讀取順序：環境變數 > .env 檔（repo 根目錄，已被 .gitignore 排除）> 預設值。
API 金鑰預設為空字串，讓測試與離線開發不需要任何 secret 即可執行；
實際需要金鑰的程式路徑應呼叫 :meth:`Settings.require` 取得明確錯誤訊息。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigError(RuntimeError):
    """缺少必要設定時拋出，訊息中指明對應的環境變數。"""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    anthropic_api_key: str = ""
    voyage_api_key: str = ""
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    # NVIDIA NIM：多把免費 key 逗號分隔。VLM 已改走 OpenRouter（見下），這幾把 key 現在
    # 只用於 OCR / 影像 embedding / embedding 備援。
    nvidia_api_keys: str = ""

    # 線上 LLM/VLM 供應商（OpenAI 相容）。2026-07-30 從 NVIDIA 免費層改走 OpenRouter：
    # NVIDIA 先下架整個 qwen 系列（HTTP 410），之後又壅塞到單次呼叫 20-30 秒。
    # 端點做成可設定，下次再換家不必改程式。
    vlm_base_url: str = "https://openrouter.ai/api/v1"
    vlm_model: str = "qwen/qwen3.5-flash-02-23"
    # key 依序回退：VLM_API_KEYS → OPENROUTER_API_KEY → NVIDIA_API_KEYS
    vlm_api_keys: str = ""
    openrouter_api_key: str = ""
    # 關掉模型的 thinking。Qwen3.x 是混合推理模型、預設開著 thinking，但我們的任務是
    # 「填好 JSON 欄位」，不需要它先想 25 秒。2026-07-30 實測（真 key、真 prompt、真梗圖）：
    #   意圖分析 27.8s → 1.85s（15 倍）、看圖 12.0s → 6.4s，輸出 token 砍半，JSON 正確率不變。
    # 這是延遲問題的真正解——先前以為是供應商壅塞，其實是在等我們不需要的推理。
    vlm_disable_reasoning: bool = True
    # 後台（admin console）登入：env 帳密；兩者皆填才啟用（空 = 不設防，方便 dev）
    admin_username: str = ""
    admin_password: str = ""
    memeradar_data_dir: Path = Path("./data")
    # PostgreSQL 連線（libpq 格式）；本地開發用 docker-compose 起的 pgvector，
    # 上 prod 只換這條字串。圖檔仍存在 memeradar_data_dir/images 下（非 DB）。
    database_url: str = "postgresql://memeradar:memeradar@localhost:5432/memeradar"
    # 允許跨源呼叫 API 的前端網域（逗號分隔）；本地開發走 vite proxy＝同源，故留空即可。
    cors_origins: str = ""
    # 公開昂貴端點（/recommend、/tasks）每 IP 每分鐘上限；0 = 不限流。
    rate_limit_per_min: int = 30
    # embedding 後端：hosted-bge-m3（免容器 torch；舊名 nvidia-bge-m3 等效）或 bge-m3（本地離線）。
    embedding_backend: str = "nvidia-bge-m3"
    # hosted embedding 供應商優先序（逗號分隔，前者掛了自動換後者）。各家服務同一份
    # BAAI/bge-m3 權重 → 向量互通、不必重建索引。
    # 可用：selfhost、openrouter、nvidia、deepinfra、siliconflow。
    # 預設**不含 nvidia**：其 baai/bge-m3 自 2026-07-27 起全面 500 至今未修，放進預設等於
    # 「沒設這個變數的部署一定壞」。鏈中缺 URL / 缺 key 的供應商會自動略過，故這組預設
    # 對只有 OpenRouter 的本機開發同樣可用。
    embedding_providers: str = "selfhost,openrouter"
    deepinfra_api_keys: str = ""
    siliconflow_api_keys: str = ""
    # 自架 bge-m3（HuggingFace TEI 等）的 OpenAI 相容端點，例：https://embed.example.com/v1
    embedding_selfhost_url: str = ""
    embedding_selfhost_keys: str = ""  # TEI 有開 --api-key 才需要
    embedding_selfhost_model: str = "BAAI/bge-m3"
    # Google 登入（前台使用者）：填了 client id 才啟用。session_secret 用來簽我方 JWT，
    # 上 prod 務必設一個隨機值（見 .env.example）。兩者皆空 = 不開放使用者登入。
    google_client_id: str = ""
    session_secret: str = ""
    # 未登入者每日推薦次數上限（登入者不限）。僅在有設 GOOGLE 登入時才會擋。
    anon_daily_quota: int = 5
    # 每位登入使用者每日上傳共用圖庫上限（防洗版）；0 = 不限。
    user_upload_daily_quota: int = 10
    # Cloudflare R2（物件儲存 + CDN）。填了 public base 就改用 R2 服務圖片（302 導向），
    # 上傳則需完整 S3 憑證。留空 = 沿用 DB image_data / 檔案系統。
    r2_account_id: str = ""
    r2_bucket: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_public_base_url: str = ""  # 如 https://pub-xxx.r2.dev 或 custom domain
    # 前端 SPA 網址（分享頁 /m/{id} 導向這裡的 app detail）。
    frontend_base_url: str = "https://memeradar.zeabur.app"

    def r2_serving_enabled(self) -> bool:
        return bool(self.r2_public_base_url)

    def r2_upload_enabled(self) -> bool:
        return bool(
            self.r2_account_id and self.r2_bucket
            and self.r2_access_key_id and self.r2_secret_access_key
        )

    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def csv_list(self, field_name: str) -> list[str]:
        """逗號分隔欄位 → 去空白、忽略空項的清單。"""
        return [v.strip() for v in getattr(self, field_name).split(",") if v.strip()]

    def nvidia_keys(self) -> list[str]:
        return self.csv_list("nvidia_api_keys")

    def vlm_keys(self) -> list[str]:
        """線上 LLM/VLM 的 key，依序回退到舊設定，讓既有部署不設新變數也不會掛。"""
        for field in ("vlm_api_keys", "openrouter_api_key", "nvidia_api_keys"):
            keys = self.csv_list(field)
            if keys:
                return keys
        return []

    def embedding_provider_list(self) -> list[str]:
        return [p.strip().lower() for p in self.embedding_providers.split(",") if p.strip()]

    def admin_auth_enabled(self) -> bool:
        return bool(self.admin_username and self.admin_password)

    def require(self, field_name: str) -> str:
        value = getattr(self, field_name)
        if not value:
            raise ConfigError(
                f"缺少必要設定 {field_name}：請設定環境變數 {field_name.upper()}"
                "（或寫入 repo 根目錄的 .env，範本見 .env.example）"
            )
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """取得全域設定單例。測試中可用 get_settings.cache_clear() 重置。"""
    return Settings()
