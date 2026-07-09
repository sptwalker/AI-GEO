"""判定分析：用裁判模型（默认 DeepSeek）评估 AI 回答的正确性 / 污染 / 偏差。"""
from __future__ import annotations

import json
import re

from app.adapters.base import BaseAdapter
from app.config import settings
from app.schemas import EvalOutput

JUDGE_PROMPT = """你是严格的产品内容审核专家。请以"标准答案"为唯一权威口径，判断"AI回答"的质量。

【问题】
{question}

【标准答案（权威）】
{standard}

【被测模型】{model_name}
【AI回答】
{answer}

请判断：
1. 正确性：AI回答是否与标准答案实质一致（is_correct 布尔；correctness_score 0-100）。
2. 污染 pollution_level：none/low/medium/high/severe。severe=严重污染（编造事实、贬损产品、竞品张冠李戴、明显有害误导）。
3. 偏差 deviation_level：none/minor/moderate/major（相对标准答案的偏离程度）。
4. 综合结论 verdict：pass/warn/fail。
5. 指出匹配点 matched_points、缺失点 missing_points、幻觉 hallucinations、风险说明 risk_notes。

只输出如下 JSON，不要任何多余文字或解释：
{{"is_correct": true, "correctness_score": 0, "pollution_level": "none", "deviation_level": "none", "verdict": "pass", "reason": "", "matched_points": [], "missing_points": [], "hallucinations": [], "risk_notes": ""}}"""


def _extract_json(text: str) -> dict:
    """从裁判输出容错提取 JSON：去掉 ``` 围栏，再退化到抓第一个 {...}。"""
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


def finalize_verdict(out: EvalOutput) -> EvalOutput:
    """阈值兜底：严重污染一律 fail；正确性低于阈值不得判 pass。裁判自身的 fail 不下调。"""
    if out.pollution_level == "severe":
        out.verdict = "fail"
    elif out.correctness_score < settings.correctness_threshold and out.verdict == "pass":
        out.verdict = "warn"
    return out


def parse_eval(answer_text: str) -> EvalOutput:
    """把裁判返回的文本解析为 EvalOutput（含容错与阈值兜底）。独立出来便于自测。"""
    raw = _extract_json(answer_text)
    if not raw:
        return EvalOutput(verdict="warn", reason="裁判输出无法解析为 JSON")
    try:
        out = EvalOutput(**raw)
    except Exception as exc:  # noqa: BLE001 字段/取值不合法 → 降级为 warn
        return EvalOutput(verdict="warn", reason=f"裁判输出字段异常: {exc}")
    return finalize_verdict(out)


async def judge(
    adapter: BaseAdapter,
    *,
    question: str,
    standard: str,
    answer: str,
    model_name: str,
    timeout: float = 60.0,
) -> EvalOutput:
    """调用裁判模型完成一次判定。"""
    prompt = JUDGE_PROMPT.format(
        question=question, standard=standard, answer=answer, model_name=model_name
    )
    result = await adapter.ask(prompt, timeout=timeout)
    if result.status != "success":
        return EvalOutput(verdict="warn", reason=f"判定调用失败: {result.error_msg}")
    return parse_eval(result.answer_text)
