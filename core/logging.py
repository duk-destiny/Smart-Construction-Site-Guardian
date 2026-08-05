"""统一日志：轻量封装 stdlib logging，首次调用配置 stderr handler。

设计原则（代码规范 §3/§4.2）：core 为最底层，仅依赖 stdlib，不引第三方。
get_logger(name) 首次调用时为 root logger 装一个 StreamHandler(stderr)，
格式「时间 级别 模块: 消息」；后续调用直接取已配置 logger，不重复装 handler。
"""
from __future__ import annotations

import logging
import sys

_configured = False


def get_logger(name: str) -> logging.Logger:
    """返回指定 name 的 logger；首次调用完成全局 handler 配置。"""
    global _configured
    log = logging.getLogger(name)
    if not _configured:
        root = logging.getLogger()
        if not root.handlers:
            handler = logging.StreamHandler(sys.stderr)
            handler.setFormatter(logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s: %(message)s"))
            root.addHandler(handler)
            root.setLevel(logging.INFO)
        _configured = True
    return log
