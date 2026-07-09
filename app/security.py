"""密码哈希：stdlib pbkdf2（不引第三方依赖）。每用户独立盐，常量时间比对。"""
from __future__ import annotations

import hashlib
import hmac
import secrets

_ITERATIONS = 200_000


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """返回 (hash_hex, salt_hex)。salt 缺省则随机生成。"""
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), _ITERATIONS)
    return dk.hex(), salt


def verify_password(password: str, pw_hash: str, salt: str) -> bool:
    calc, _ = hash_password(password, salt)
    return hmac.compare_digest(calc, pw_hash)
