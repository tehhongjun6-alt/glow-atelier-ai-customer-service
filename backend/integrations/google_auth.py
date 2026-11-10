"""Google 登录（OAuth）

Google Calendar 和 Gmail 都要"代表某个 Google 账号"操作，所以要先登录授权一次：
1. 第一次运行：浏览器会打开 Google 登录页 → 选账号 → 按"允许"
2. 授权结果存在 backend/secrets/token.json
3. 之后程序自动用 token.json，过期了会自动更新，不用再登录

一次就把第 4 步（Calendar）和第 5 步（Gmail）要的权限都拿齐，之后不用重新授权。

用法（在 backend 资料夹里）：
    python -m integrations.google_auth     第一次登录授权
"""

from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

SECRETS_DIR = Path(__file__).resolve().parent.parent / "secrets"
CLIENT_FILE = SECRETS_DIR / "credentials.json"   # 从 Google Cloud 下载的 OAuth 客户端
TOKEN_FILE = SECRETS_DIR / "token.json"          # 登录后自动产生

SCOPES = [
    "https://www.googleapis.com/auth/calendar",       # 第 4 步：建日历、新增／修改／删除活动
    "https://www.googleapis.com/auth/gmail.modify",   # 第 5 步：读信、发信、回信、标记已读
]


RELOGIN_HINT = "请删除 backend/secrets/token.json，再运行 python -m integrations.google_auth 重新登录。"


class GoogleAuthError(RuntimeError):
    """Google 授权有问题（没登录过、过期、被撤销）。服务器上不能自动登录，只能报错请人处理。"""


def get_credentials(interactive=False):
    """取得 Google 授权。

    interactive=False（服务器、API、自动回信用）：授权有问题就直接报错，不会卡住等人登录。
    interactive=True（在终端手动运行时）：授权有问题就打开浏览器重新登录。
    """
    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())       # 过期了：用 refresh token 自动换新的
        except RefreshError as error:
            # 最常见：app 还在"测试"状态，refresh token 7 天就失效（invalid_grant）
            if not interactive:
                raise GoogleAuthError(f"Google 授权已过期或被撤销（{error}）。{RELOGIN_HINT}") from error
            creds = None

    if not creds or not creds.valid:
        if not interactive:
            raise GoogleAuthError(f"还没有有效的 Google 授权。{RELOGIN_HINT}")
        if not CLIENT_FILE.exists():
            raise FileNotFoundError(
                f"找不到 {CLIENT_FILE}。请先在 Google Cloud 建立 OAuth 客户端（Desktop app），"
                "下载 JSON 后改名为 credentials.json 放进 backend/secrets/"
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_FILE), SCOPES)
        creds = flow.run_local_server(port=0)   # 打开浏览器让你登录

    SECRETS_DIR.mkdir(exist_ok=True)
    TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    return creds


def status():
    """给 /api/health 用：Google 授权现在能不能用。"""
    try:
        get_credentials()
        return {"ok": True}
    except Exception as error:
        return {"ok": False, "error": str(error)}


if __name__ == "__main__":
    get_credentials(interactive=True)
    print(f"✅ 登录成功，授权已存到 {TOKEN_FILE}")
