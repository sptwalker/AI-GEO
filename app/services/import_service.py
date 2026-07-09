"""批量导入题库：支持纯文本（每行一题）、CSV、JSON，按归一化正文去重。"""
from __future__ import annotations

import csv
import io
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Question
from app.schemas import ImportResult

_FIELDS = ("content", "category", "product", "remark")


def _norm(text: str | None) -> str:
    """归一化用于去重：压缩空白 + 小写。"""
    return " ".join((text or "").split()).lower()


def parse_rows(
    *, text: str | None = None, filename: str | None = None, content: bytes | None = None
) -> tuple[list[dict], list[str]]:
    """把输入解析成 [{content, category, product, remark}]，返回 (rows, errors)。"""
    rows: list[dict] = []
    errors: list[str] = []

    if text:  # 文本框粘贴，每行一题
        rows = [{"content": ln.strip()} for ln in text.splitlines() if ln.strip()]
        return rows, errors

    if content is None:
        return rows, ["无输入内容"]

    name = (filename or "").lower()
    try:
        if name.endswith(".json"):
            for item in json.loads(content.decode("utf-8")):
                if isinstance(item, str) and item.strip():
                    rows.append({"content": item.strip()})
                elif isinstance(item, dict) and item.get("content"):
                    rows.append({k: item.get(k) for k in _FIELDS})
        elif name.endswith(".csv"):
            reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig")))
            for r in reader:
                if r.get("content"):
                    rows.append({k: (r.get(k) or None) for k in _FIELDS})
        else:  # 其它当纯文本
            rows = [
                {"content": ln.strip()}
                for ln in content.decode("utf-8").splitlines()
                if ln.strip()
            ]
    except Exception as exc:  # noqa: BLE001 解析失败作为可读错误返回，不抛
        errors.append(f"解析失败: {exc}")
    return rows, errors


def import_questions(
    db: Session, *, text: str | None = None, filename: str | None = None, content: bytes | None = None
) -> ImportResult:
    """导入并去重入库。"""
    rows, errors = parse_rows(text=text, filename=filename, content=content)
    res = ImportResult(errors=errors)

    # ponytail: MVP 把已有题目正文全量载入内存去重；题量很大时改为 content_hash 列 + 唯一索引
    existing = {_norm(c) for c in db.scalars(select(Question.content))}
    seen: set[str] = set()
    for r in rows:
        content_text = (r.get("content") or "").strip()
        if not content_text:
            res.failed += 1
            continue
        key = _norm(content_text)
        if key in existing or key in seen:
            res.skipped += 1
            continue
        seen.add(key)
        db.add(
            Question(
                content=content_text,
                category=r.get("category"),
                product=r.get("product"),
                remark=r.get("remark"),
            )
        )
        res.created += 1
    db.commit()
    return res
