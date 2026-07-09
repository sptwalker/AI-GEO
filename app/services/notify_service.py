"""飞书报警：自定义群机器人 Webhook 发送 + 批次异常汇总。

支持可选"加签"密钥。发送结果写入 alert_log 便于排查。渠道收口在此，
未来加邮件/其它渠道只需扩展 send_* 与 notify。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
from datetime import datetime

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AlertLog, EvalResult, LLMModel, QALog, RunBatch

logger = logging.getLogger(__name__)


def gen_sign(timestamp: str, secret: str) -> str:
    """飞书自定义机器人加签算法：hmac-sha256(key=f'{ts}\\n{secret}', msg='') 再 base64。"""
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(string_to_sign.encode("utf-8"), b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def send_feishu(text: str) -> tuple[bool, str]:
    """发送纯文本到飞书。返回 (是否成功, 说明)。未配置 webhook 返回 skipped 说明。"""
    url = settings.alert_webhook_url
    if not url:
        return False, "未配置 webhook"
    body: dict = {"msg_type": "text", "content": {"text": text}}
    if settings.alert_webhook_secret:
        ts = str(int(time.time()))
        body["timestamp"] = ts
        body["sign"] = gen_sign(ts, settings.alert_webhook_secret)
    try:
        resp = httpx.post(url, json=body, timeout=10)
        data = resp.json()
        # 飞书成功返回 {"code":0,...} 或旧版 {"StatusCode":0}
        ok = data.get("code") == 0 or data.get("StatusCode") == 0
        return ok, "ok" if ok else str(data)[:300]
    except Exception as exc:  # noqa: BLE001 网络/解析异常统一返回失败
        return False, str(exc)[:300]


def alert_batch(db: Session, batch: RunBatch) -> None:
    """批次跑完后：若命中 fail / 严重污染，则汇总并发飞书，记 alert_log。

    ponytail: 手动与定时批次共用此逻辑；未配置 webhook 时安静跳过（仅记 skipped）。
    如需仅对定时批次报警，在调用处按 batch.trigger_type 过滤即可。
    """
    rows = db.execute(
        select(EvalResult, QALog)
        .join(QALog, EvalResult.qa_log_id == QALog.id)
        .where(QALog.batch_id == batch.id)
        .where((EvalResult.verdict == "fail") | (EvalResult.pollution_level == "severe"))
    ).all()
    if not rows:
        return

    mnames = {m.id: m.display_name for m in db.scalars(select(LLMModel))}
    lines = [
        f"【AI-GEO 监控报警】批次「{batch.name}」",
        f"命中 {len(rows)} 条异常 · 总{batch.total}/成功{batch.success_count}/失败{batch.fail_count}",
        "————",
    ]
    for ev, log in rows[:5]:
        q = (log.question_snapshot or "")[:28]
        m = mnames.get(log.model_id, log.model_id)
        lines.append(f"· [{m}] {q}… → {ev.verdict}/污染{ev.pollution_level}/{ev.correctness_score}分")
    if len(rows) > 5:
        lines.append(f"…另有 {len(rows) - 5} 条，详见后台查询")
    text = "\n".join(lines)

    ok, msg = send_feishu(text)
    status = "sent" if ok else ("skipped" if msg == "未配置 webhook" else "failed")
    if status == "failed":
        logger.warning("飞书报警发送失败: %s", msg)
    db.add(
        AlertLog(
            batch_id=batch.id,
            level="warning",
            title=f"批次 {batch.id} 命中 {len(rows)} 条异常",
            content=text,
            channel="feishu",
            status=status,
            error_msg=None if ok else msg,
            sent_at=datetime.now() if ok else None,
        )
    )
    db.commit()
