"""共享的 Jinja2 模板环境。"""
from pathlib import Path

from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# 模板里用 is_admin(request) 判断是否管理员，据此显示/隐藏写操作控件
templates.env.globals["is_admin"] = lambda request: bool(
    request and request.session.get("role") == "admin"
)
