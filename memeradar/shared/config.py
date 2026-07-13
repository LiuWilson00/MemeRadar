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
    # NVIDIA NIM（VLM 標註）：多把免費 key 逗號分隔，輪流用以分攤速率限制
    nvidia_api_keys: str = ""
    nvidia_vlm_model: str = "qwen/qwen3.5-122b-a10b"
    # 後台（admin console）登入：env 帳密；兩者皆填才啟用（空 = 不設防，方便 dev）
    admin_username: str = ""
    admin_password: str = ""
    memeradar_data_dir: Path = Path("./data")
    # PostgreSQL 連線（libpq 格式）；本地開發用 docker-compose 起的 pgvector，
    # 上 prod 只換這條字串。圖檔仍存在 memeradar_data_dir/images 下（非 DB）。
    database_url: str = "postgresql://memeradar:memeradar@localhost:5432/memeradar"
    # 允許跨源呼叫 API 的前端網域（逗號分隔）；本地開發走 vite proxy＝同源，故留空即可。
    cors_origins: str = ""

    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    def nvidia_keys(self) -> list[str]:
        return [k.strip() for k in self.nvidia_api_keys.split(",") if k.strip()]

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
