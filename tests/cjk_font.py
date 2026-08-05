"""跨平台定位 CJK 字体，供测试生成中文 PDF。"""
from __future__ import annotations

import os

_CANDIDATES = (
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\msyh.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
)


def cjk_font_path() -> str:
    """返回第一个存在的 CJK 字体路径，找不到时回退到 SimHei。"""
    for path in _CANDIDATES:
        if os.path.exists(path):
            return path
    return _CANDIDATES[0]
