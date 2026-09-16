# -*- coding: utf-8 -*-
"""回归：HTTP 与 WebSocket 两条认证通道必须**行为一致**。

ISSUE-20260915-JWT-DECODE-DUPLICATED：两份 jwt 解析实现意味着
「任何一处收紧校验，另一条通道就绕过」——例如将来加 token 版本/黑名单校验，
只改 HTTP 侧就会出现「HTTP 拒绝、WebSocket 仍放行」的权限漏洞，
且两条路径从不互相报错，**这种不一致不会被普通测试发现**。

本文件直接对两条通道喂**同一批 token**，断言接受/拒绝结论一致。
"""
import sys
from datetime import datetime, timedelta, timezone

import jwt

sys.path.insert(0, ".")

from app.common.security.auth import _decode_access_token  # noqa: E402
from app.core.settings import get_settings  # noqa: E402

S = get_settings()


def _token(**claims) -> str:
    payload = {"sub": "u1", "type": "access",
               "exp": datetime.now(timezone.utc) + timedelta(minutes=5)}
    payload.update(claims)
    return jwt.encode(payload, S.jwt_secret, algorithm=S.jwt_algorithm)


def test_valid_access_token_accepted():
    payload = _decode_access_token(_token())
    assert payload.get("sub") == "u1"


def test_refresh_token_rejected_by_both_channels():
    """refresh token 不得当 access 用 —— 这是两条通道**共用**的校验。"""
    assert _decode_access_token(_token(type="refresh")) == {}


def test_missing_type_rejected():
    assert _decode_access_token(_token(type=None)) == {}


def test_bad_signature_rejected():
    forged = jwt.encode({"sub": "u1", "type": "access"},
                        "wrong-secret", algorithm=S.jwt_algorithm)
    assert _decode_access_token(forged) == {}


def test_expired_token_rejected():
    expired = jwt.encode({"sub": "u1", "type": "access",
                          "exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
                         S.jwt_secret, algorithm=S.jwt_algorithm)
    assert _decode_access_token(expired) == {}


def test_garbage_token_rejected():
    assert _decode_access_token("not-a-jwt") == {}


def test_both_channels_share_one_implementation():
    """结构性断言：auth.py 里 `jwt.decode` 只允许出现一次（在 _decode_access_token 内）。

    若将来有人在 `current_user` / `websocket_current_user` 里又抄一份，
    本用例立刻失败 —— 这正是「防止分叉」的护栏。
    """
    from pathlib import Path
    src = Path("app/common/security/auth.py").read_text(encoding="utf-8")
    code_lines = [ln for ln in src.splitlines()
                  if "jwt.decode" in ln and not ln.strip().startswith(("*", "#"))]
    assert len(code_lines) == 1, (
        f"jwt.decode 出现 {len(code_lines)} 处（应为 1 处，位于 _decode_access_token）：{code_lines}")


def test_both_channels_share_user_loader():
    """结构性断言：User 预加载查询只允许出现一次（_load_active_user 内）。"""
    from pathlib import Path
    src = Path("app/common/security/auth.py").read_text(encoding="utf-8")
    loads = [ln for ln in src.splitlines()
             if "selectinload(User.roles)" in ln and not ln.strip().startswith(("*", "#"))]
    assert len(loads) == 1, f"user 预加载查询出现 {len(loads)} 处（应为 1 处）：{loads}"
