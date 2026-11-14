"""统一的 Logging 设定（程序启动时调用一次）

每一条 log 都会：
1. 印在终端（一边运行一边看）
2. 写进 backend/logs/<文件名>（事后翻查；logs/ 已加进 .gitignore，不会上传）

格式：时间 | 等级 | 哪个模块 | 内容
    2026-11-14 15:30:02 | INFO    | glow.email | 找到 1 封新信
"""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = Path(__file__).resolve().parent / "logs"
LOG_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(filename, level=logging.INFO):
    if logging.getLogger().handlers:   # 已经设定过（例如被 import 第二次），不要重复加
        return
    LOG_DIR.mkdir(exist_ok=True)
    # 文件超过 1 MB 就换新的，最多留 3 个旧档，避免越积越大
    file_handler = RotatingFileHandler(LOG_DIR / filename, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    logging.basicConfig(
        level=level,
        format=LOG_FORMAT,
        datefmt=DATE_FORMAT,
        handlers=[logging.StreamHandler(), file_handler],
    )
    # Google SDK 底层用 httpx 连网，每个请求都会写一条 INFO，会把我们自己的 log 淹没；只留 WARNING 以上
    for noisy in ("httpx", "httpx2"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
