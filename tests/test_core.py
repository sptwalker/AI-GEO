"""最小自测：判定解析与导入解析的关键逻辑（不依赖数据库/网络）。

运行：python -m tests.test_core   或   pytest
这些是 ponytail 要求的「一处可运行校验」：判定兜底与导入解析一旦被改坏即失败。
"""
import json

from app.services.eval_service import parse_eval
from app.services.export_service import LOG_HEADER, build_csv
from app.services.import_service import _norm, parse_rows
from app.services.notify_service import gen_sign


def test_parse_eval_forces_fail_on_severe():
    # 严重污染必须兜底为 fail，即使裁判给了 pass 和高分
    out = parse_eval('{"is_correct": true, "correctness_score": 90, "pollution_level": "severe", "verdict": "pass"}')
    assert out.verdict == "fail"


def test_parse_eval_low_score_not_pass():
    # 正确性明显偏低时不得判 pass
    out = parse_eval('{"is_correct": true, "correctness_score": 20, "pollution_level": "none", "verdict": "pass"}')
    assert out.verdict == "warn"


def test_parse_eval_handles_fenced_json():
    out = parse_eval('```json\n{"correctness_score": 80, "pollution_level": "none", "verdict": "pass"}\n```')
    assert out.correctness_score == 80 and out.verdict == "pass"


def test_parse_eval_garbage_is_warn():
    out = parse_eval("模型抽风了，没有返回 JSON")
    assert out.verdict == "warn"


def test_parse_rows_text():
    rows, errors = parse_rows(text="问题一\n\n问题二\n")
    assert [r["content"] for r in rows] == ["问题一", "问题二"]
    assert not errors


def test_parse_rows_json_bytes():
    data = json.dumps([{"content": "A", "category": "x"}, "B"]).encode("utf-8")
    rows, errors = parse_rows(filename="q.json", content=data)
    assert len(rows) == 2 and rows[0]["category"] == "x" and not errors


def test_norm_dedup():
    assert _norm("  Hello   World ") == _norm("hello world")


def test_feishu_sign_stable():
    # 飞书加签：同一 (timestamp, secret) 结果稳定、非空、base64
    import base64

    s1 = gen_sign("1700000000", "mysecret")
    s2 = gen_sign("1700000000", "mysecret")
    assert s1 == s2 and s1 and base64.b64decode(s1)  # 可被 base64 解码
    assert gen_sign("1700000001", "mysecret") != s1  # 时间戳变则签名变


def test_validate_cron():
    from app.scheduler.service import validate_cron

    assert validate_cron("0 9 * * *")
    assert validate_cron("*/30 * * * *")
    assert not validate_cron("bad cron")
    assert not validate_cron("99 99 * * *")


def test_build_csv_bom_and_rows():
    # 带 UTF-8 BOM（Excel 中文不乱码）、含表头与数据、逗号被正确转义
    data = build_csv(LOG_HEADER, [["2026-01-01 09:00", "DeepSeek", "问,含逗号", "答", "success", "pass", "none", "none", 90, 3]])
    assert data.startswith("﻿".encode("utf-8"))
    text = data.decode("utf-8-sig")
    assert "时间,模型" in text and "DeepSeek" in text
    assert '"问,含逗号"' in text  # csv 对逗号字段加引号


def test_password_hash_verify():
    from app.security import hash_password, verify_password

    h, salt = hash_password("s3cret")
    assert verify_password("s3cret", h, salt)
    assert not verify_password("wrong", h, salt)
    # 随机盐：两次哈希不同
    h2, salt2 = hash_password("s3cret")
    assert h2 != h and salt2 != salt


def test_clean_answer_strips_noise_keeps_content():
    from app.adapters.web_automation import clean_answer

    pats = [r"您更喜欢哪个回答", r"回答\s*[12]\b", r"内容由AI生成[，,][^\n]*"]
    raw = "元宝是腾讯的AI助手。\n\n您更喜欢哪个回答 回答 1 回答 2\n内容由AI生成，仅供参考"
    out = clean_answer(raw, pats)
    assert "元宝是腾讯的AI助手" in out
    assert "您更喜欢哪个回答" not in out and "内容由AI生成" not in out
    # 无 patterns 时原样返回（仅归整空白）
    assert clean_answer("正常回答", []) == "正常回答"


def test_parse_eval_risk_and_pollution_types():
    # M5：四级风险 + 污染类型，严重污染兜底为 severe/fail
    out = parse_eval(
        '{"risk_level":"severe","pollution_level":"severe","pollution_types":["false_info","defamation"],"correctness_score":10}'
    )
    assert out.risk_level == "severe" and out.verdict == "fail"
    assert "false_info" in out.pollution_types
    # 正确性过低但未标风险 -> 至少中度
    out2 = parse_eval('{"risk_level":"normal","correctness_score":20,"pollution_level":"none"}')
    assert out2.risk_level == "moderate"


def test_aggregate_votes_majority_and_tiebreak():
    from app.schemas import EvalOutput
    from app.services.eval_service import aggregate_votes

    # 多数 normal（严重那票是少数）
    v = aggregate_votes([
        EvalOutput(risk_level="normal", correctness_score=90),
        EvalOutput(risk_level="normal", correctness_score=88),
        EvalOutput(risk_level="severe", correctness_score=10),
    ])
    assert v.risk_level == "normal"
    # 平票取更重
    v2 = aggregate_votes([EvalOutput(risk_level="normal"), EvalOutput(risk_level="severe")])
    assert v2.risk_level == "severe"


def test_consistency_parse():
    from app.services.eval_service import parse_consistency

    c = parse_consistency('{"consistency_score":40,"is_consistent":false,"contradiction":true,"reason":"x"}')
    assert c.contradiction and c.consistency_score == 40


def test_normalize_coerces_variant_values():
    # 裁判返回中文/变体取值(deviation=low, risk=严重, 污染类型中文)不应导致整条判定作废
    out = parse_eval(
        '{"risk_level":"严重","correctness_score":30,"deviation_level":"low","pollution_level":"高","pollution_types":["虚假信息","抹黑"]}'
    )
    assert out.risk_level == "severe" and out.deviation_level == "minor" and out.pollution_level == "high"
    assert "false_info" in out.pollution_types and "defamation" in out.pollution_types
    assert "字段异常" not in out.reason and "无法解析" not in out.reason


if __name__ == "__main__":
    for _name, _fn in list(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
            print("ok", _name)
    print("ALL PASS")
