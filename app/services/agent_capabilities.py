from __future__ import annotations

from dataclasses import dataclass

from app.models.auth import User


@dataclass(frozen=True)
class AgentCapability:
    code: str
    required_roles: frozenset[str] = frozenset()
    required_permissions_any: frozenset[str] = frozenset()
    internal_only: bool = False


class AgentCapabilityRegistry:
    """Deterministic pre-routing gate for Supervisor-visible Agent capabilities."""

    _capabilities = (
        AgentCapability("CustomerServiceAgent", required_roles=frozenset({"customer"})),
        AgentCapability("CustomerRecommendationCapability", required_roles=frozenset({"customer"})),
        AgentCapability("CustomerTradeDraftCapability", required_roles=frozenset({"customer"})),
        AgentCapability("CustomerRiskExplanationCapability", required_roles=frozenset({"customer"})),
        AgentCapability("DataAnalysisAgent", required_permissions_any=frozenset({"asset:read", "customer:read_all", "customer:read_assigned", "risk:alert_read", "order:review", "operation:read_staff"}), internal_only=True),
        AgentCapability("AdvisorAgent", required_roles=frozenset({"advisor"}), internal_only=True),
        AgentCapability("RiskAgent", required_permissions_any=frozenset({"risk:alert_read", "risk:alert_handle", "risk:alert_action", "risk:scan_execute"}), internal_only=True),
        AgentCapability("BusinessAgent", required_permissions_any=frozenset({"operation:read_staff", "order:review", "transfer:submit_staff"}), internal_only=True),
        AgentCapability("AdminAgent", required_permissions_any=frozenset({"audit:read", "admin:write"}), internal_only=True),
    )

    @staticmethod
    def role_codes(user: User) -> set[str]:
        return {role.code for role in user.roles}

    @staticmethod
    def permission_codes(user: User) -> set[str]:
        return {permission.code for role in user.roles for permission in role.permissions}

    def available_for(self, user: User) -> list[str]:
        if user.is_super_admin:
            return [capability.code for capability in self._capabilities]
        roles = self.role_codes(user)
        permissions = self.permission_codes(user)
        is_customer = "customer" in roles
        available: list[str] = []
        for capability in self._capabilities:
            if capability.internal_only and is_customer:
                continue
            if capability.required_roles and not (roles & capability.required_roles):
                continue
            if capability.required_permissions_any and not (permissions & capability.required_permissions_any):
                continue
            available.append(capability.code)
        return available

    def denied_for(self, user: User) -> list[str]:
        available = set(self.available_for(user))
        return [capability.code for capability in self._capabilities if capability.code not in available]
