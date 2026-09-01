from __future__ import annotations

import asyncio
from types import SimpleNamespace

from workers.authorization_worker import decode_value, handle_message


class FakeClient:
    def __init__(self):
        self.values = {}

    async def incr(self, key):
        self.values[key] = int(self.values.get(key, 0)) + 1
        return self.values[key]


class FakeQueue:
    def __init__(self):
        self.client = FakeClient()


def test_decode_value_accepts_redis_encoded_json_and_plain_values():
    assert decode_value('"advisor-1"') == "advisor-1"
    assert decode_value("advisor-2") == "advisor-2"
    assert decode_value("") == ""


def test_handle_message_invalidates_scope_and_ignores_unexpected_event():
    async def run():
        queue = FakeQueue()
        valid = SimpleNamespace(message_id="1-0", data={"event_type": "authorization.subject_scope_invalidate", "advisor_id": '"advisor-1"', "event_id": "evt-1"})
        ignored = SimpleNamespace(message_id="2-0", data={"event_type": "other.event"})
        assert await handle_message(queue, valid) == "1"
        assert await handle_message(queue, ignored) == "ignored"
        assert queue.client.values == {"wm:subject-scope-version:advisor-1": 1}

    asyncio.run(run())


def test_handle_message_rejects_missing_advisor_id_without_mutation():
    async def run():
        queue = FakeQueue()
        invalid = SimpleNamespace(message_id="3-0", data={"event_type": "authorization.subject_scope_invalidate", "advisor_id": ""})
        try:
            await handle_message(queue, invalid)
        except ValueError as exc:
            assert "advisor_id" in str(exc)
        else:
            raise AssertionError("missing advisor_id must fail")
        assert queue.client.values == {}

    asyncio.run(run())
