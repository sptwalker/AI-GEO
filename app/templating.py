"""共享的 Jinja2 模板环境。"""
from pathlib import Path

from fastapi.templating import Jinja2Templates

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# 模板里用 is_admin(request) 判断是否管理员，据此显示/隐藏写操作控件
templates.env.globals["is_admin"] = lambda request: bool(
    request and request.session.get("role") == "admin"
)
# 复核权限：admin 或 reviewer（可标注/改标准答案）
templates.env.globals["can_review"] = lambda request: bool(
    request and request.session.get("role") in ("admin", "reviewer")
)
# M5 四级风险的中文与配色（Bootstrap）
templates.env.globals["RISK_CN"] = {
    "normal": "正常", "minor": "轻微偏差", "moderate": "中度偏移", "severe": "严重",
}
templates.env.globals["RISK_CLS"] = {
    "normal": "success", "minor": "info", "moderate": "warning", "severe": "danger",
}
