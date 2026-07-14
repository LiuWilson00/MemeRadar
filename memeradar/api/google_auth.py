"""Google ID token 驗證（正式用；測試改注入 stub verifier）。

用 google-auth 的 ``verify_oauth2_token`` 驗簽章、``aud == client_id``、
``iss``、``exp``；任何不符會丟例外（我方端點捕捉後回 401）。只用來確認身分，
不代使用者呼叫任何 Google API，故不需要 client secret。
"""

from __future__ import annotations

from collections.abc import Callable


def build_google_verifier(client_id: str) -> Callable[[str], dict]:
    """回傳 callable(credential)->claims；無效 token 丟例外。"""
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    request = google_requests.Request()

    def verify(credential: str) -> dict:
        return id_token.verify_oauth2_token(credential, request, client_id)

    return verify
