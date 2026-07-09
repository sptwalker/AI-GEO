"""手动初始化数据库：建表 + 初始化模型配置。

用法（仓库根目录）：
    python -m scripts.init_db
或：
    python scripts/init_db.py
"""
import pathlib
import sys

# 允许以 `python scripts/init_db.py` 直接运行时也能 import app.*
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.bootstrap import ensure_schema_and_seed  # noqa: E402

if __name__ == "__main__":
    ensure_schema_and_seed()
    print("数据库初始化完成：表结构就绪，默认模型已补齐。")
