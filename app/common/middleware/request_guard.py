# -*- coding: utf-8 -*-
"""请求护栏中间件：请求体大小限制 + 全局超时 + 按 IP 限流 + GZip。

## 为什么用**纯 ASGI** 中间件而不是 `BaseHTTPMiddleware`

Starlette 的 `BaseHTTPMiddleware` **只处理 HTTP**——WebSocket 请求不走它。
本项目最需要观测/保护的实时客户 Agent 通道恰恰是 WebSocket，
所以护栏若建在 `BaseHTTPMiddleware` 上，会**天然漏掉 ws**。

纯 ASGI 形态（`async def __call__(scope, receive, send)`）可同时看到
`scope["type"]` 为 `"http"` 与 `"websocket"` 的流量，
故本中间件按 type 分派：HTTP 做全部护栏，WS 至少能记录/限流。

## 各护栏的取舍

| 护栏 | 超限行为 | 为什么这样定 |
|---|---|---|
| 请求体大小 | **413** | 按 `Content-Length` 预检（便宜），无该头则信任（流式 body 无法预知） |
| 全局超时 | **504** | 兜底，必须长于所有业务超时；只覆盖 HTTP 响应阶段 |
| 限流 | **429** | 复用既有 `RedisRateLimiter`（固定窗口）；**默认关闭**，避免影响压测 |
| GZip | 自动压缩 | 仅对超过阈值的响应，且仅在客户端声明支持时 |

## 关键设计判断

1. **默认全部关闭或宽松**：这些护栏一旦开启会改变既有回归的行为
   （限流会让并发测试出现 429、超时会让长 LLM 调用 504）。
   故默认值保守，由 env 显式打开；**且每个都可用 0 关闭**。
2. **限流 key 优先取真实 IP**：经反代时 `request.client.host` 是反代 IP，
   会把所有用户算成同一个 → 必须优先读 `X-Forwarded-For` 首段。
   **注意**：XFF 可被客户端伪造，仅在**可信反代**后使用；直连时应忽略。
   当前实现取 XFF 首段（配合差距清单 E4 的网关部署）。
3. **超时不能"掐断已发出的响应"**：用 `asyncio.wait_for` 包裹下游调用；
   若响应已开始发送则无法回退状态码，故超时只在**响应未开始**时返回 504。
"""
from __future__ import annotations

import asyncio
import time
from typing import Any

from app.common.logging.config import get_logger

logger = get_logger(__name__)

# 这些路径不做限流（健康检查/指标/文档），否则探活会被打成 429。
_RATE_LIMIT_EXEMPT_PREFIXES = ("/api/v1/health", "/docs", "/openapi.json", "/redoc", "/metrics")


def _client_ip(scope: dict) -> str:
    """取客户端 IP：优先 X-Forwarded-For 首段（经可信反代时才是真实客户端）。"""
    for name, value in scope.get("headers") or []:
        if name == b"x-forwarded-for":
            first = value.decode("latin-1").split(",")[0].strip()
            if first:
                return first
    client = scope.get("client")
    return client[0] if client else "unknown"


# 限流器槽位。
# 为什么用模块级变量而不是构造参数：`add_middleware` 在**模块导入时**执行，
# 那时 Redis 尚未连接（导入期建连接会污染所有测试与 CLI 脚本）。
# 故由 `main._install_rate_limiter()` 在 lifespan 内把实例写入此槽位。
# 默认 None = 不限流。
_LIMITER: Any = None


def set_limiter(limiter: Any) -> None:
    """由 `main._install_rate_limiter()` 在 lifespan 内调用。"""
    global _LIMITER
    _LIMITER = limiter


def get_limiter() -> Any:
    return _LIMITER


class _BodyTooLarge(Exception):
    pass


class _RequestTimeout(Exception):
    pass


class RequestGuardMiddleware:
    """纯 ASGI 中间件：请求体大小 + 全局超时 + 限流 + GZip。

    `limiter` 为可选的 `RedisRateLimiter`（None = 不限流）；
    `redis_client` 缺失时自动跳过限流（降级而非报错）。
    """

    def __init__(self, app, *, max_body_bytes: int = 0, timeout_seconds: float = 0.0,
                 limiter=None, rate_limit: int = 0, rate_window_seconds: int = 60,
                 gzip_min_size: int = 0) -> None:
        self.app = app
        self.max_body_bytes = int(max_body_bytes or 0)
        self.timeout_seconds = float(timeout_seconds or 0)
        self.limiter = limiter
        self.rate_limit = int(rate_limit or 0)
        self.rate_window_seconds = max(1, int(rate_window_seconds or 60))
        self.gzip_min_size = int(gzip_min_size or 0)
        # GZip 用 Starlette 现成实现（它对 Accept-Encoding 的判断已处理好）
        if self.gzip_min_size > 0:
            from starlette.middleware.gzip import GZipMiddleware
            self._gzip = GZipMiddleware(app, minimum_size=self.gzip_min_size)
        else:
            self._gzip = None

    # ---- 限流 ----
    async def _rate_limited(self, scope: dict) -> bool:
        limiter = self.limiter if self.limiter is not None else _LIMITER
        if self.rate_limit <= 0 or limiter is None:
            return False
        path = scope.get("path", "")
        if path.startswith(_RATE_LIMIT_EXEMPT_PREFIXES):
            return False
        from app.common.rate_limit import RateLimitPolicy
        try:
            allowed, _ = await limiter.check(
                _client_ip(scope),
                RateLimitPolicy(limit=self.rate_limit, window_seconds=self.rate_window_seconds))
            return not allowed
        except Exception as exc:  # noqa: BLE001 - 限流器故障**不应**拒绝正常请求
            # 关键判断：Redis 挂掉时若"保守地拒绝"，会把整个服务变成不可用。
            # 正确的降级方向是**放行**（限流是保护措施，不是业务前置条件）。
            logger.warning("rate_limiter_failed err=%s — 放行本次请求", type(exc).__name__)
            return False

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        # --- 限流（http 与 ws 都做）---
        if await self._rate_limited(scope):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1013})  # 1013 = try again later
                return
            await _send_simple(send, 429, b'{"code":"RATE_LIMITED","message":"rate limit exceeded"}')
            return

        # --- 请求体大小（仅 http：ws 无 Content-Length 语义）---
        if scope["type"] == "http" and self.max_body_bytes > 0:
            length = _content_length(scope)
            if length is not None and length > self.max_body_bytes:
                logger.warning("request_body_too_large path=%s length=%s limit=%s",
                               scope.get("path"), length, self.max_body_bytes)
                await _send_simple(
                    send, 413,
                    b'{"code":"PAYLOAD_TOO_LARGE","message":"request body too large"}')
                return

        if scope["type"] != "http" or self.timeout_seconds <= 0:
            await self._inner(scope, receive, send)
            return

        # --- 全局超时（仅 http）---
        started = time.monotonic()
        try:
            await asyncio.wait_for(self._inner(scope, receive, send), timeout=self.timeout_seconds)
        except asyncio.TimeoutError:
            logger.error("request_timeout path=%s elapsed_ms=%.0f limit_s=%s",
                         scope.get("path"), (time.monotonic() - started) * 1000, self.timeout_seconds)
            # 注意：若下游已开始发送响应（status 已发出），此处再发 504 会抛
            # "Unexpected message received" —— 故用 try 包住，且如实说明"可能已来不及"。
            try:
                await _send_simple(
                    send, 504,
                    b'{"code":"REQUEST_TIMEOUT","message":"request timed out"}')
            except Exception:  # noqa: BLE001 - 响应已开始发送，无法再改状态码
                logger.warning("request_timeout_response_already_started path=%s",
                               scope.get("path"))

    async def _inner(self, scope: dict, receive: Any, send: Any) -> None:
        if self._gzip is not None:
            await self._gzip(scope, receive, send)
        else:
            await self.app(scope, receive, send)


def _content_length(scope: dict) -> int | None:
    for name, value in scope.get("headers") or []:
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None


async def _send_simple(send: Any, status: int, body: bytes) -> None:
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json; charset=utf-8"),
                            (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})
