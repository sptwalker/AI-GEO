"""告警（M5 多渠道）：飞书 / 企业微信 群机器人 + SMTP 邮件。

触发口径：批次中出现「中高危」判定（risk_level >= alert_risk_threshold）或「口径矛盾」
即推送到所有已配置渠道。发送结果写 alert_log 便于排查。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import smtplib
import time
from datetime import datetime
from email.mime.text import MIMEText

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import AlertLog, ConsistencyResult, EvalResult, LLMModel, QALog, RunBatch

logger = logging.getLogger(__name__)

# 达到中高危的风险级
_HIGH_RISK = ("moderate", "severe")
_RISK_CN = {"moderate": "中度偏移", "severe": "严重污染/错误"}


# ---------------- 各渠道 ----------------


def gen_sign(timestamp: str, secret: str) -> str:
    """飞书自定义机器人加签：hmac-sha256(key=f'{ts}\\n{secret}', msg='') 再 base64。"""
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(string_to_sign.encode("utf-8"), b"", hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


def send_feishu(text: str) -> tuple[bool, str]:
    url = settings.alert_webhook_url
    if not url:
        return False, "未配置"
    body: dict = {"msg_type": "text", "content": {"text": text}}
    if settings.alert_webhook_secret:
        ts = str(int(time.time()))
        body["timestamp"] = ts
        body["sign"] = gen_sign(ts, settings.alert_webhook_secret)
    try:
        data = httpx.post(url, json=body, timeout=10).json()
        ok = data.get("code") == 0 or data.get("StatusCode") == 0
        return ok, "ok" if ok else str(data)[:200]
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:200]


def send_wecom(text: str) -> tuple[bool, str]:
    """企业微信群机器人（自定义 webhook）。"""
    url = settings.wecom_webhook_url
    if not url:
        return False, "未配置"
    try:
        data = httpx.post(url, json={"msgtype": "text", "text": {"content": text}}, timeout=10).json()
        ok = data.get("errcode") == 0
        return ok, "ok" if ok else str(data)[:200]
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:200]


def send_email(subject: str, body: str) -> tuple[bool, str]:
    """SMTP 邮件告警（stdlib）。465=SSL，587=STARTTLS，其它按明文。"""
    if not (settings.smtp_host and settings.alert_mail_to):
        return False, "未配置"
    sender = settings.smtp_from or settings.smtp_user or "ai-geo@localhost"
    recipients = [x.strip() for x in settings.alert_mail_to.split(",") if x.strip()]
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    try:
        if settings.smtp_port == 465:
            smtp = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=10)
        else:
            smtp = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=10)
            if settings.smtp_port == 587:
                smtp.starttls()
        with smtp:
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password or "")
            smtp.sendmail(sender, recipients, msg.as_string())
        return True, "ok"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)[:200]


def _dispatch(db: Session, batch_id: int, title: str, text: str) -> None:
    """推送到所有已配置渠道，各自记 alert_log；未配置渠道静默跳过。"""
    for channel, fn in (
        ("feishu", lambda: send_feishu(text)),
        ("wecom", lambda: send_wecom(text)),
        ("email", lambda: send_email(title, text)),
    ):
        ok, msg = fn()
        if not ok and msg == "未配置":
            continue  # 未配置的渠道不记录
        if not ok:
            logger.warning("告警渠道 %s 发送失败: %s", channel, msg)
        db.add(
            AlertLog(
                batch_id=batch_id,
                level="warning",
                title=title,
                content=text,
                channel=channel,
                status="sent" if ok else "failed",
                error_msg=None if ok else msg,
                sent_at=datetime.now() if ok else None,
            )
        )
    db.commit()


# ---------------- 批次告警 ----------------


def alert_batch(db: Session, batch: RunBatch) -> None:
    """批次跑完：汇总中高危判定 + 口径矛盾，推送多渠道。无命中则静默。"""
    risky = db.execute(
        select(EvalResult, QALog)
        .join(QALog, EvalResult.qa_log_id == QALog.id)
        .where(QALog.batch_id == batch.id)
        .where(EvalResult.risk_level.in_(_HIGH_RISK))
    ).all()
    contra = list(
        db.scalars(
            select(ConsistencyResult).where(
                ConsistencyResult.batch_id == batch.id, ConsistencyResult.contradiction.is_(True)
            )
        )
    )
    if not risky and not contra:
        return

    mnames = {m.id: m.display_name for m in db.scalars(select(LLMModel))}
    lines = [
        f"【AI-GEO 风险告警】批次「{batch.name}」",
        f"中高危 {len(risky)} 条 · 口径矛盾 {len(contra)} 处 · 总{batch.total}/成功{batch.success_count}/失败{batch.fail_count}",
        "————",
    ]
    for ev, log in risky[:5]:
        m = mnames.get(log.model_id, log.model_id)
        ptypes = "/".join(ev.pollution_types or []) or "无"
        lines.append(
            f"· [{m}] {(log.question_snapshot or '')[:24]}… → 风险:{_RISK_CN.get(ev.risk_level, ev.risk_level)}"
            f" 污染:{ptypes} 正确性:{ev.correctness_score}"
        )
    for c in contra[:3]:
        m = mnames.get(c.model_id, c.model_id)
        lines.append(f"· [口径矛盾][{m}] 问题#{c.question_id} 一致性{c.consistency_score}分")
    if len(risky) > 5 or len(contra) > 3:
        lines.append("…更多详见后台查询")
    text = "\n".join(lines)
    _dispatch(db, batch.id, f"AI-GEO 风险告警·批次{batch.id}", text)
