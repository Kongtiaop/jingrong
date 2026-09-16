# -*- coding: utf-8 -*-
"""请求护栏中间件：请求体大小 / 全局超时 / 限流 / GZip。

每条护栏都验证「生效」与「不误伤」两个方向 —— 只测"能拦"会漏掉误拦。
"""
import asyncio
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, ".")

from app.common.middleware.request_guard import (  # noqa: E402
    RequestGuardMiddleware, get_limiter, set_limiter,
)


def _app(**kwargs) -> FastAPI:
    """构造一个只含护栏的干净 app（不污染全局 app，也无需 DB）。"""
    t = FastAPI()

    @t.get("/ping")
    async def ping():
        return {"ok": True}

    @t.get("/api/v1/health")
    async def health():
        return {"ok": True}

    @t.get("/slow")
    async def slow():
        await asyncio.sleep(2)
        return {"ok": True}

    @t.get("/big")
    async def big():
        return {"data": "A" * 5000}

    t.add_middleware(RequestGuardMiddleware, **{
        "max_body_bytes": 0, "timeout_seconds": 0, "rate_limit": 0,
        "rate_window_seconds": 60, "gzip_min_size": 0, **kwargs})
    return t


# ---------- 请求体大小 ----------

def test_large_body_rejected_with_413():
    c = TestClient(_app(max_body_bytes=1024))
    r = c.post("/ping", content=b"x" * 2048)
    assert r.status_code == 413
    assert "PAYLOAD_TOO_LARGE" in r.text


def test_small_body_not_rejected():
    """**不能误拦**：正常大小的请求必须放行。"""
    c = TestClient(_app(max_body_bytes=1024))
    r = c.post("/ping", content=b"x" * 100)
    assert r.status_code != 413


def test_body_limit_disabled_when_zero():
    c = TestClient(_app(max_body_bytes=0))
    assert c.post("/ping", content=b"x" * 5000).status_code != 413


# ---------- 全局超时 ----------

def test_slow_request_times_out_with_504():
    c = TestClient(_app(timeout_seconds=0.3))
    assert c.get("/slow").status_code == 504


def test_fast_request_not_timed_out():
    """**不能误杀**：比阈值快的请求必须正常返回。"""
    c = TestClient(_app(timeout_seconds=5))
    assert c.get("/ping").status_code == 200


def test_timeout_disabled_when_zero():
    c = TestClient(_app(timeout_seconds=0))
    assert c.get("/ping").status_code == 200


# ---------- 限流 ----------

class _CountingLimiter:
    def __init__(self, allow: int):
        self.n = 0
        self.allow = allow

    async def check(self, key, policy):
        self.n += 1
        return (self.n <= self.allow, max(0, self.allow - self.n))


@pytest.fixture(autouse=True)
def _reset_limiter():
    """避免模块级限流槽位在用例间泄漏（它是全局状态）。"""
    set_limiter(None)
    yield
    set_limiter(None)


def test_rate_limit_returns_429_after_threshold():
    set_limiter(_CountingLimiter(allow=2))
    c = TestClient(_app(rate_limit=2))
    codes = [c.get("/ping").status_code for _ in range(4)]
    assert codes == [200, 200, 429, 429]


def test_health_path_exempt_from_rate_limit():
    """探活/指标路径不能被限流，否则整个服务会被误判为不健康。"""
    set_limiter(_CountingLimiter(allow=1))
    c = TestClient(_app(rate_limit=1))
    assert c.get("/ping").status_code == 200
    assert c.get("/ping").status_code == 429
    assert c.get("/api/v1/health").status_code == 200


def test_rate_limit_disabled_by_default():
    c = TestClient(_app(rate_limit=0))
    assert [c.get("/ping").status_code for _ in range(5)] == [200] * 5


def test_limiter_failure_fails_open():
    """**关键**：限流器（Redis）故障时必须**放行**，不能保守拒绝。

    若故障时返回 429，一次 Redis 抖动就会让整个服务不可用 ——
    限流是保护措施，不是业务前置条件。
    """
    class _Boom:
        async def check(self, key, policy):
            raise RuntimeError("redis down")

    set_limiter(_Boom())
    c = TestClient(_app(rate_limit=5))
    assert c.get("/ping").status_code == 200


def test_limiter_uses_forwarded_for_when_present():
    """经反代时必须按真实客户端 IP 限流，否则所有用户共用一个桶。"""
    seen: list[str] = []

    class _Rec:
        async def check(self, key, policy):
            seen.append(key)
            return (True, 0)

    set_limiter(_Rec())
    c = TestClient(_app(rate_limit=10))
    c.get("/ping", headers={"X-Forwarded-For": "203.0.113.9, 10.0.0.1"})
    assert seen == ["203.0.113.9"], f"未取 XFF 首段：{seen}"


# ---------- GZip ----------

def test_gzip_applied_when_client_accepts():
    c = TestClient(_app(gzip_min_size=1024))
    r = c.get("/big", headers={"Accept-Encoding": "gzip"})
    assert r.headers.get("content-encoding") == "gzip"


def test_gzip_skipped_when_client_declines():
    """客户端声明不支持时**不得**压缩（否则客户端无法解码）。"""
    c = TestClient(_app(gzip_min_size=1024))
    r = c.get("/big", headers={"Accept-Encoding": "identity"})
    assert not r.headers.get("content-encoding")


def test_gzip_skipped_for_small_response():
    c = TestClient(_app(gzip_min_size=1024))
    r = c.get("/ping", headers={"Accept-Encoding": "gzip"})
    assert not r.headers.get("content-encoding")


# ---------- 全局 app 的接线 ----------

def test_global_app_has_request_guard_outermost():
    """结构性断言：护栏必须在**最外层**（后 add 的在最外层）。

    外层才能先于业务拦下大 body/超限请求，且被拒的请求也进得了日志。
    """
    from app.main import app
    names = [m.cls.__name__ for m in app.user_middleware]
    assert names[0] == "RequestGuardMiddleware", f"护栏不在最外层：{names}"
    assert "TraceIdMiddleware" in names


def test_global_app_cors_origins_from_settings():
    """CORS 白名单必须来自配置（此前硬编码，生产换域名要改代码）。"""
    from app.core.settings import get_settings
    from app.main import app
    cors = next(m for m in app.user_middleware if m.cls.__name__ == "CORSMiddleware")
    configured = [o.strip() for o in get_settings().cors_allow_origins.split(",") if o.strip()]
    assert cors.kwargs["allow_origins"] == configured
    assert "http://127.0.0.1:5173" in configured
