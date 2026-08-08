from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class KnowledgeScope:
    knowledge_base_ids: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()
    permission_levels: tuple[str, ...] = ()

    @classmethod
    def public_customer(cls) -> "KnowledgeScope":
        return cls(categories=("company_info", "company_business", "faq", "workflow", "policy", "risk_public"), permission_levels=("public", "customer_visible"))

    @classmethod
    def internal_advisor(cls) -> "KnowledgeScope":
        return cls(categories=("company_business", "policy", "risk", "suitability"), permission_levels=("public", "internal", "advisor"))

    def allows(self, *, knowledge_base_id: str, category: str, permission_level: str) -> bool:
        return ((not self.knowledge_base_ids or knowledge_base_id in self.knowledge_base_ids)
                and (not self.categories or category in self.categories)
                and (not self.permission_levels or permission_level in self.permission_levels))
