"""Cloudflare R2（S3 相容）物件儲存：上傳圖檔 + 組公開 CDN URL。

填了 R2 設定就把圖片放 R2、由其公開網址（r2.dev 或 custom domain 走 Cloudflare CDN）
直接服務，前端 <img> 不再經過 API/DB。未設定時呼叫端回退 DB image_data / 檔案系統。
"""

from __future__ import annotations

from memeradar.shared.config import Settings

_IMAGE_CACHE_CONTROL = "public, max-age=31536000, immutable"  # 梗圖不可變，長快取
_CONTENT_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                  ".webp": "image/webp"}


def _client(settings: Settings):
    import boto3

    return boto3.client(
        "s3",
        endpoint_url=f"https://{settings.r2_account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.r2_access_key_id,
        aws_secret_access_key=settings.r2_secret_access_key,
        region_name="auto",
    )


def content_type_for(key: str) -> str:
    import os

    return _CONTENT_TYPES.get(os.path.splitext(key)[1].lower(), "application/octet-stream")


def put_image(settings: Settings, key: str, data: bytes) -> None:
    """上傳圖檔到 R2（key 即 image_uri，如 images/m_xxx.png），帶長快取標頭。"""
    _client(settings).put_object(
        Bucket=settings.r2_bucket, Key=key, Body=data,
        ContentType=content_type_for(key), CacheControl=_IMAGE_CACHE_CONTROL,
    )


def public_url(base: str, image_uri: str) -> str:
    """image_uri（如 images/m_xxx.png）→ 公開 URL（base/images/m_xxx.png）。"""
    return f"{base.rstrip('/')}/{image_uri.lstrip('/')}"
