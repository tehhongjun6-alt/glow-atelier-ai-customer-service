"""建立 2 个 demo 员工帐号（Supabase Auth），登录后台用

- 密码随机产生，存进 backend/secrets/demo_staff.txt（secrets/ 不会上传 GitHub），终端不会显示密码
- 直接用 Supabase 管理员 API 设定 role（app_metadata），不用再跑 006 SQL
- 帐号已经存在就跳过，不会改掉原本的密码

用法（在 backend 资料夹里）：
    python create_demo_staff.py
"""

import secrets
from pathlib import Path

from rag.store import supabase

STAFF = [
    {"email": "tehhongjun6+amy@gmail.com", "name": "前台 Amy", "role": "staff"},
    {"email": "tehhongjun6+jessica@gmail.com", "name": "店长 Jessica", "role": "admin"},
]
OUTPUT = Path(__file__).resolve().parent / "secrets" / "demo_staff.txt"


def existing_emails():
    return {u.email for u in supabase.auth.admin.list_users()}


def main():
    have = existing_emails()
    lines = []
    for person in STAFF:
        if person["email"] in have:
            print(f"已经有这个帐号，跳过：{person['email']}")
            continue
        password = secrets.token_urlsafe(12)
        supabase.auth.admin.create_user({
            "email": person["email"],
            "password": password,
            "email_confirm": True,                        # 不寄验证信，直接可以登录
            "app_metadata": {"role": person["role"]},     # 员工身份：只有后台能改
            "user_metadata": {"name": person["name"]},
        })
        lines.append(f"{person['name']}（{person['role']}）\n  邮箱：{person['email']}\n  密码：{password}\n")
        print(f"✅ 建立了 {person['name']}：{person['email']}")

    if lines:
        OUTPUT.parent.mkdir(exist_ok=True)
        with OUTPUT.open("a", encoding="utf-8") as f:
            f.write("Glow Atelier 后台 demo 员工帐号\n\n" + "\n".join(lines) + "\n")
        print(f"密码存在 {OUTPUT}（不会上传 GitHub）")


if __name__ == "__main__":
    main()
