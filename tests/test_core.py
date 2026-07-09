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


if __name__ == "__main__":
    for _name, _fn in list(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
            print("ok", _name)
    print("ALL PASS")
