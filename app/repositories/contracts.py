from __future__ import annotations

from typing import Any, Protocol


class TradingUnitOfWorkProtocol(Protocol):
    users: Any
    profiles: Any
    risks: Any
    products: Any
    accounts: Any
    orders: Any
    holdings: Any
    assets: Any
    trades: Any
    status_history: Any

    async def commit(self, session: Any) -> None: ...
    async def rollback(self, session: Any) -> None: ...


class TradingWriteRepositoryProtocol(Protocol):
    async def save(self, session: Any, record: Any) -> Any: ...
    async def update(self, session: Any, record: Any) -> Any: ...

class TradingAccountWriteProtocol(Protocol):
    async def save(self, session: Any, record: Any) -> Any: ...
    async def update_balances(self, session: Any, account_id: str, available_balance: Any, frozen_balance: Any) -> Any: ...


from dataclasses import dataclass
from typing import Any

@dataclass(frozen=True)
class CustomerSummaryQueryRow:
    user: Any
    profile: Any
    risk: Any
    holding_count: int
    asset: Any

    @property
    def user_id(self):
        return getattr(self.user, "id", None)

    def normalized(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "username": getattr(self.user, "username", None),
            "display_name": getattr(self.user, "display_name", None),
            "status": getattr(self.user, "status", None),
            "customer_type": getattr(self.profile, "customer_type", None),
            "customer_tier": getattr(self.profile, "customer_tier", None),
            "risk_level": getattr(self.risk, "risk_level", None),
            "risk_score": getattr(self.risk, "score", None),
            "holding_count": int(self.holding_count or 0),
            "investable_asset": str(getattr(self.asset, "investable_asset", 0) or 0).rstrip("0").rstrip(".") or "0",
        }
