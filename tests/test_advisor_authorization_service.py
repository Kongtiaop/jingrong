from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.services.advisor_authorization_service import AdvisorAuthorizationService


class FakeRedis:
    def __init__(self):
        self.values = {}

    async def incr(self, key):
        self.values[key] = int(self.values.get(key, 0)) + 1
        return self.values[key]


def test_invalidate_subject_scopes_deduplicates_advisors():
    async def run():
        redis = FakeRedis()
        await AdvisorAuthorizationService.invalidate_subject_scopes(redis, ["advisor-a", "advisor-a", "advisor-b"])
        assert redis.values == {
            "wm:subject-scope-version:advisor-a": 1,
            "wm:subject-scope-version:advisor-b": 1,
        }

    asyncio.run(run())


def test_enqueue_scope_invalidation_events_deduplicates_advisors():
    class Session:
        def __init__(self):
            self.events = []
        def add(self, value):
            self.events.append(value)

    session = Session()
    event_ids = AdvisorAuthorizationService.enqueue_scope_invalidation_events(
        session, advisor_ids=["advisor-a", "advisor-a", "advisor-b"], mutation_source="test"
    )
    assert len(event_ids) == 2
    assert {event.event_type for event in session.events} == {"authorization.subject_scope_invalidate"}
    assert {event.payload_json["advisor_id"] for event in session.events} == {"advisor-a", "advisor-b"}
