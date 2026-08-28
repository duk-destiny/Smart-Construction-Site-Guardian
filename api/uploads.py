"""上传适配：把 FastAPI UploadFile 适配成服务层依赖的鸭子类型接口。

services.task_entry.save_media / admin_console.import_pdf /
order_service.submit_rectification 依赖 Streamlit UploadedFile 的
getvalue()/name 接口——本适配只做接口形态转换，不含业务逻辑。
"""
from __future__ import annotations


class UploadedLike:
    """与 Streamlit UploadedFile 同构：name + getvalue()。"""

    def __init__(self, name: str | None, data: bytes) -> None:
        self.name = name or "upload.bin"
        self._data = data

    def getvalue(self) -> bytes:
        return self._data
