"""CSV 导出：把行数据编码为带 BOM 的 UTF-8 CSV（Excel 打开中文不乱码）。

build_csv 为纯函数，便于自测；路由负责查数据、拼行、返回下载响应。
"""
from __future__ import annotations

import csv
import io

LOG_HEADER = ["时间", "模型", "问题", "回答", "状态", "风险", "污染类型", "正确性", "语义", "批次ID"]


def build_csv(header: list[str], rows: list[list]) -> bytes:
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(header)
    for r in rows:
        writer.writerow(["" if c is None else c for c in r])
    # BOM 前缀，保证 Excel 以 UTF-8 打开
    return ("﻿" + out.getvalue()).encode("utf-8")


def qalog_rows(logs, model_names: dict) -> list[list]:
    """把 QALog(含 eval) 列表转成 CSV 行。"""
    rows = []
    for log in logs:
        ev = log.eval
        rows.append(
            [
                log.asked_at.strftime("%Y-%m-%d %H:%M:%S") if log.asked_at else "",
                model_names.get(log.model_id, log.model_id),
                log.question_snapshot,
                log.answer_text if log.status == "success" else f"[失败] {log.error_msg or ''}",
                log.status,
                ev.risk_level if ev else "",
                "/".join(ev.pollution_types or []) if ev else "",
                ev.correctness_score if ev else "",
                ev.semantic_score if ev else "",
                log.batch_id,
            ]
        )
    return rows
