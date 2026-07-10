"""智能判定引擎（M5）：语义/事实/逻辑/缺失级判定 + 污染类型识别 + 四级风险 +
多裁判投票降误判 + 人工标记 few-shot 回流 + 口径一致性检测。

裁判默认 DeepSeek（可配）。所有对外函数保持"失败不抛、降级为 warn/未知"，不打断批次。
"""
from __future__ import annotations

import asyncio
import json
import re

from app.adapters.base import BaseAdapter
from app.config import settings
from app.schemas import RISK_ORDER, ConsistencyOutput, EvalOutput

# ---------------- 判定提示词 ----------------

JUDGE_PROMPT = """你是严格的产品内容审核专家。请以"标准答案"为**该产品唯一权威口径与事实基准**，对"AI回答"做**语义级**判定——不要只看字面，要判断真实含义。

重要：即使 AI 回答在通用常识上看似合理，只要与标准答案的产品口径**实质不符**，也应按偏离/错误判定（correctness 以"是否与标准答案一致"为准，而非与通用常识一致）。

{fewshot}【问题】
{question}

【标准答案（该产品权威口径）】
{standard}

【被测模型】{model_name}
【AI回答】
{answer}

请从以下维度判断（务必按语义而非字面）：
1. 语义正确性与事实性：回答是否与标准答案实质一致（is_correct；correctness_score 0-100 表示与标准口径的契合度；semantic_score 语义相似度 0-100）。
2. 识别"字面相近但语义歪曲/逻辑错误/信息缺失"：distortions（歪曲）、logic_errors（逻辑错误）、missing_info（关键信息缺失）、hallucinations（编造）。
3. 恶意污染：pollution_level(none/low/medium/high/severe) 与 pollution_types（可多选：false_info=虚假信息, defamation=负面抹黑, rumor=不实谣言, exaggeration=违规夸大宣传）。
4. 四级风险 risk_level：normal=正常, minor=轻微偏差, moderate=中度偏移, severe=严重污染/错误。与标准口径实质冲突或含严重污染应判 moderate 及以上。

只输出如下 JSON，不要多余文字：
{{"risk_level":"normal","is_correct":true,"correctness_score":0,"semantic_score":0,"pollution_level":"none","pollution_types":[],"deviation_level":"none","reason":"","distortions":[],"logic_errors":[],"missing_info":[],"hallucinations":[],"risk_notes":""}}"""

CONSISTENCY_PROMPT = """以下是同一问题在多次询问下、同一AI模型给出的多个回答。请判断它们口径是否一致、有无前后矛盾/输出不稳定。

【问题】{question}

{answers}

只输出 JSON：{{"consistency_score":0,"is_consistent":true,"contradiction":false,"reason":""}}
其中 consistency_score 0-100（越高越一致），contradiction=是否存在实质性矛盾。"""


def build_fewshot(cases: list[dict]) -> str:
    """把人工标记的纠错案例拼成 few-shot 前缀，纠偏裁判。cases 为空则返回空串。"""
    if not cases:
        return ""
    lines = ["【历史人工校准案例（供参考，请对齐这些判定口径）】"]
    for i, c in enumerate(cases, 1):
        lines.append(
            f"案例{i}｜问题：{c['question'][:60]}｜AI回答：{c['answer'][:80]}｜"
            f"人工认定风险：{c['label_risk']}"
        )
    return "\n".join(lines) + "\n\n"


# ---------------- JSON 容错解析 + 取值归一 ----------------

# 不同模型会返回中英/变体取值，统一映射到规范集，避免个别字段作废整条判定
_RISK_MAP = {
    "normal": "normal", "正常": "normal", "无": "normal", "ok": "normal",
    "minor": "minor", "轻微": "minor", "轻微偏差": "minor", "轻度": "minor", "low": "minor", "低": "minor",
    "moderate": "moderate", "中度": "moderate", "中度偏移": "moderate", "中": "moderate", "medium": "moderate",
    "severe": "severe", "严重": "severe", "严重污染": "severe", "严重错误": "severe", "高": "severe",
    "high": "severe", "critical": "severe", "严重污染/错误": "severe",
}
_POL_MAP = {
    "none": "none", "无": "none", "否": "none", "no": "none",
    "low": "low", "低": "low", "轻微": "low",
    "medium": "medium", "中": "medium", "中等": "medium", "moderate": "medium",
    "high": "high", "高": "high",
    "severe": "severe", "严重": "severe",
}
_DEV_MAP = {
    "none": "none", "无": "none", "no": "none",
    "minor": "minor", "轻微": "minor", "轻度": "minor", "low": "minor", "小": "minor",
    "moderate": "moderate", "中度": "moderate", "medium": "moderate", "中": "moderate",
    "major": "major", "严重": "major", "重大": "major", "重度": "major", "high": "major", "大": "major", "severe": "major",
}
_PTYPE_MAP = {
    "false_info": "false_info", "虚假信息": "false_info", "虚假": "false_info", "不实信息": "false_info",
    "defamation": "defamation", "负面抹黑": "defamation", "抹黑": "defamation", "诋毁": "defamation", "负面": "defamation",
    "rumor": "rumor", "不实谣言": "rumor", "谣言": "rumor", "传闻": "rumor",
    "exaggeration": "exaggeration", "违规夸大宣传": "exaggeration", "夸大": "exaggeration",
    "夸大宣传": "exaggeration", "过度宣传": "exaggeration", "虚假宣传": "exaggeration",
}


def _coerce(v, mapping: dict, default: str) -> str:
    if v is None:
        return default
    raw = str(v).strip()
    return mapping.get(raw.lower(), mapping.get(raw, default))


def normalize(raw: dict) -> dict:
    """把裁判返回的字段值归一到规范集，并夹紧分数。"""
    raw = dict(raw)
    raw["risk_level"] = _coerce(raw.get("risk_level"), _RISK_MAP, "normal")
    raw["pollution_level"] = _coerce(raw.get("pollution_level"), _POL_MAP, "none")
    raw["deviation_level"] = _coerce(raw.get("deviation_level"), _DEV_MAP, "none")
    pts = raw.get("pollution_types") or []
    if isinstance(pts, str):
        pts = [pts]
    mapped = [_PTYPE_MAP.get(str(t).strip().lower(), _PTYPE_MAP.get(str(t).strip())) for t in pts]
    raw["pollution_types"] = [t for t in mapped if t]
    for k in ("correctness_score", "semantic_score"):
        try:
            raw[k] = max(0, min(100, int(float(raw.get(k, 0)))))
        except (TypeError, ValueError):
            raw[k] = 0
    return raw


def _extract_json(text: str) -> dict:
    if not text:
        return {}
    cleaned = re.sub(r"```(?:json)?|```", "", text).strip()
    try:
        return json.loads(cleaned)
    except Exception:  # noqa: BLE001
        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:  # noqa: BLE001
                return {}
    return {}


def _derive_verdict(risk: str) -> str:
    return {"normal": "pass", "minor": "warn", "moderate": "warn", "severe": "fail"}.get(
        risk, "warn"
    )


def _risk_idx(risk: str) -> int:
    try:
        return RISK_ORDER.index(risk)
    except ValueError:
        return 0


def finalize(out: EvalOutput) -> EvalOutput:
    """安全兜底（降漏判）：严重污染必升级为 severe；正确性过低至少 moderate。再派生 verdict。"""
    if out.pollution_level == "severe" or (
        any(t in ("false_info", "defamation", "rumor") for t in out.pollution_types)
        and out.pollution_level in ("high", "severe")
    ):
        out.risk_level = "severe"
    if out.correctness_score < settings.correctness_threshold and _risk_idx(
        out.risk_level
    ) < _risk_idx("moderate"):
        out.risk_level = "moderate"
    out.verdict = _derive_verdict(out.risk_level)
    return out


def parse_eval(answer_text: str) -> EvalOutput:
    """把裁判文本解析为 EvalOutput（容错 + 取值归一 + 安全兜底）。独立便于自测。"""
    raw = _extract_json(answer_text)
    if not raw:
        return EvalOutput(risk_level="minor", verdict="warn", reason="裁判输出无法解析为 JSON")
    try:
        out = EvalOutput(**normalize(raw))
    except Exception as exc:  # noqa: BLE001
        return EvalOutput(risk_level="minor", verdict="warn", reason=f"裁判输出字段异常: {exc}")
    return finalize(out)


# ---------------- 多裁判投票聚合 ----------------


def aggregate_votes(votes: list[EvalOutput]) -> EvalOutput:
    """多票聚合：风险取多数(平票取更重)，分数取均值，污染取最重，问题项取并集。"""
    votes = [v for v in votes if v is not None]
    if not votes:
        return EvalOutput(risk_level="minor", verdict="warn", reason="无有效判定票")
    if len(votes) == 1:
        return votes[0]

    # 风险：按出现次数排序，平票取更重（RISK_ORDER 越靠后越重）
    counts: dict[str, int] = {}
    for v in votes:
        counts[v.risk_level] = counts.get(v.risk_level, 0) + 1
    top = max(counts.values())
    tied = [r for r, n in counts.items() if n == top]
    risk = max(tied, key=lambda r: RISK_ORDER.index(r) if r in RISK_ORDER else 0)

    def avg(vals):
        return round(sum(vals) / len(vals))

    pol_order = ["none", "low", "medium", "high", "severe"]
    dev_order = ["none", "minor", "moderate", "major"]
    half = len(votes) / 2
    ptype_counts: dict[str, int] = {}
    for v in votes:
        for t in set(v.pollution_types):
            ptype_counts[t] = ptype_counts.get(t, 0) + 1

    def union(attr, cap=6):
        seen, out = set(), []
        for v in votes:
            for x in getattr(v, attr):
                if x not in seen:
                    seen.add(x)
                    out.append(x)
        return out[:cap]

    merged = EvalOutput(
        risk_level=risk,
        is_correct=sum(v.is_correct for v in votes) > len(votes) / 2,
        correctness_score=avg([v.correctness_score for v in votes]),
        semantic_score=avg([v.semantic_score for v in votes]),
        pollution_level=max((v.pollution_level for v in votes), key=lambda p: pol_order.index(p)),
        pollution_types=[t for t, n in ptype_counts.items() if n >= half],
        deviation_level=max((v.deviation_level for v in votes), key=lambda d: dev_order.index(d)),
        reason=next((v.reason for v in votes if v.risk_level == risk and v.reason), votes[0].reason),
        logic_errors=union("logic_errors"),
        missing_info=union("missing_info"),
        distortions=union("distortions"),
        hallucinations=union("hallucinations"),
    )
    return finalize(merged)


async def _judge_once(adapter, prompt, timeout) -> EvalOutput:
    res = await adapter.ask(prompt, timeout=timeout)
    if res.status != "success":
        return EvalOutput(risk_level="minor", verdict="warn", reason=f"判定调用失败: {res.error_msg}")
    return parse_eval(res.answer_text)


async def judge(
    adapter: BaseAdapter,
    *,
    question: str,
    standard: str,
    answer: str,
    model_name: str,
    fewshot: list[dict] | None = None,
    votes: int | None = None,
    timeout: float = 60.0,
) -> tuple[EvalOutput, list[dict]]:
    """多裁判投票判定。返回 (聚合结果, 各票明细)。"""
    n = max(1, votes if votes is not None else settings.judge_votes)
    prompt = JUDGE_PROMPT.format(
        fewshot=build_fewshot(fewshot or []),
        question=question,
        standard=standard,
        answer=answer,
        model_name=model_name,
    )
    outs = await asyncio.gather(*(_judge_once(adapter, prompt, timeout) for _ in range(n)))
    merged = aggregate_votes(list(outs))
    detail = [
        {"risk_level": o.risk_level, "correctness_score": o.correctness_score,
         "pollution_level": o.pollution_level}
        for o in outs
    ]
    return merged, detail


# ---------------- 口径一致性 ----------------


def parse_consistency(answer_text: str) -> ConsistencyOutput:
    raw = _extract_json(answer_text)
    if not raw:
        return ConsistencyOutput(consistency_score=50, reason="一致性裁判输出无法解析")
    try:
        return ConsistencyOutput(**raw)
    except Exception as exc:  # noqa: BLE001
        return ConsistencyOutput(consistency_score=50, reason=f"字段异常: {exc}")


async def judge_consistency(
    adapter: BaseAdapter, *, question: str, answers: list[str], model_name: str, timeout: float = 60.0
) -> ConsistencyOutput:
    """对同一(问题,模型)的多个采样答案判定一致性。"""
    if len([a for a in answers if a]) < 2:
        return ConsistencyOutput(reason="样本不足，无法判定一致性")
    blocks = "\n".join(f"【回答{i + 1}】{(a or '')[:600]}" for i, a in enumerate(answers))
    prompt = CONSISTENCY_PROMPT.format(question=question, answers=blocks)
    res = await adapter.ask(prompt, timeout=timeout)
    if res.status != "success":
        return ConsistencyOutput(consistency_score=50, reason=f"一致性调用失败: {res.error_msg}")
    return parse_consistency(res.answer_text)
