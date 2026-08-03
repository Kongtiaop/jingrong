from __future__ import annotations

import json
from types import SimpleNamespace

from app.api.trading import get_trading_service, service

class RequestWithService:
    def __init__(self, value): self.app = SimpleNamespace(state=SimpleNamespace(trading_service=value))
class RequestWithoutService:
    def __init__(self): self.app = SimpleNamespace(state=SimpleNamespace())

def main():
    configured = object()
    result = {
        "configured_service_used": get_trading_service(RequestWithService(configured)) is configured,
        "fallback_service_used": get_trading_service(RequestWithoutService()) is service,
    }
    result["pass"] = all(result.values())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["pass"]: raise SystemExit(1)
if __name__ == '__main__': main()
