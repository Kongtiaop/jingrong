from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from uuid import uuid4

from app.core.settings import get_settings
from app.repositories.unit_of_work import create_mysql_validation_unit_of_work
from app.services.risk_assessment_service import RiskAssessmentService


async def main():
    uow = create_mysql_validation_unit_of_work(get_settings().mysql_url)
    assessment_id = str(uuid4())
    user = None
    try:
        user = await uow.database.fetch_one("SELECT id FROM users WHERE username='customer_demo'")
        async with uow.transaction() as tx:
            service = RiskAssessmentService(repository=uow.risks)
            existing = await uow.risks.get_active(tx, user['id'])
            item = await service.ensure_default(tx, user['id'])
            created = item.id != getattr(existing, 'id', None) if existing else item is not None
            await uow.risks.supersede_active(tx, user['id'])
            item.id = assessment_id
            item.status = 'active'
            item.source_type = 'compat_test'
            item.assessed_at = datetime.now(timezone.utc)
            await uow.risks.save(tx, item)
            stored = await uow.risks.get_active(tx, user['id'])
            passed = stored is not None and stored.id == assessment_id and stored.source_type == 'compat_test'
        await uow.database.execute("DELETE FROM customer_risk_assessment WHERE id=%s", (assessment_id,))
        result = {'created_or_reused_default': created, 'stored': passed, 'pass': passed}
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        if not passed:
            raise SystemExit(1)
    finally:
        await uow.close()


if __name__ == '__main__':
    asyncio.run(main())
