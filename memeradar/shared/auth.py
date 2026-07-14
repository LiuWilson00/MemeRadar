"""我方 session token：使用者用 Google 登入驗證後，由後端簽發的短憑證。

用 HS256（對稱）簽章，密鑰為 ``SESSION_SECRET``。前端存 localStorage，
之後每個請求帶 ``Authorization: Bearer <token>``；後端解出 user_id。
與後台的 HTTP Basic（admin）互不相干。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt

_ALG = "HS256"
_DEFAULT_TTL = 30 * 24 * 3600  # 30 天


def issue_session(user_id: str, secret: str, *, ttl_seconds: int = _DEFAULT_TTL) -> str:
    """簽一張帶 user_id 與到期時間的 session token。"""
    now = datetime.now(UTC)
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + timedelta(seconds=ttl_seconds),
    }
    return jwt.encode(payload, secret, algorithm=_ALG)


def verify_session(token: str, secret: str) -> str | None:
    """驗章＋檢查到期，回 user_id；任何無效（壞章 / 過期 / 亂碼）都回 None。"""
    try:
        payload = jwt.decode(token, secret, algorithms=[_ALG])
    except jwt.PyJWTError:
        return None
    sub = payload.get("sub")
    return sub if isinstance(sub, str) else None
