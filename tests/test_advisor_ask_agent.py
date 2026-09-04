"""AdvisorBoundedLoop 有界工具环：≤3 轮、白名单、审计、降级（fake provider/tools/db）。"""
import asyncio
from types import SimpleNamespace

from app.services.advisor_ask_agent import AdvisorBoundedLoop


class _FakeDb:
    def session_factory(self):
        return _FakeSession()


class _FakeSession:
    def __init__(self):
        self.added = []
    def add(self, obj):
        self.added.append(obj)
    async def commit(self):
        pass
    async def __aenter__(self):
        return self
    async def __aexit__(self, *exc):
        return False


class _FakeProvider:
    def __init__(self, decisions):
        self.decisions = list(decisions)
    async def request_tool_call(self, **kwargs):
        if not self.decisions:
            return None
        return self.decisions.pop(0)


class _FakeTools:
    def __init__(self, results):
        self.results = results
    async def execute_tool(self, session, *, name, arguments, customer_id):
        # returns (facts, text)
        return [name], self.results.get(name, "（结果）")


def _verify_audit(tool_result_calls, allowlist_ok=True):
    pass


def test_loop_runs_up_to_tool_and_annotates():
    import app.services.advisor_ask_agent as aaa
    orig = aaa.AdvisorBoundedLoop._audit_tool
    audits = []

    async def fake_audit(self, conversation_id, actor_user_id, name, arguments, had_result):
        audits.append((name, arguments, had_result))

    aaa.AdvisorBoundedLoop._audit_tool = fake_audit
    tools = _FakeTools({"find_products": "在售产品（真实）：- 中短债增利计划 | C2\n"})
    prov = _FakeProvider([
        {"name": "find_products", "arguments": {"query": "中短债"}},
        None,
    ])
    loop = AdvisorBoundedLoop(prov, tools, _FakeDb())
    final, had = asyncio.run(loop.run_to_text_user(
        conversation_id="conv1", actor_user_id="user1", customer_id="cust1",
        context_user="客户：张三\n", system="你是投顾。"))
    aaa.AdvisorBoundedLoop._audit_tool = orig
    assert had is True
    assert len(audits) == 1
    assert audits[0][0] == "find_products"
    assert "中短债增利计划" in final


def test_loop_bounds_rounds():
    import app.services.advisor_ask_agent as aaa
    audits = []
    orig = aaa.AdvisorBoundedLoop._audit_tool

    async def fake_audit(self, conversation_id, actor_user_id, name, arguments, had_result):
        audits.append(name)

    aaa.AdvisorBoundedLoop._audit_tool = fake_audit
    # 模型一直请求工具，但白名单工具只有两个；超过上限则停止
    prov = _FakeProvider([{"name": "find_products", "arguments": {}}] * 10)
    tools = _FakeTools({"find_products": "结果"})
    loop = AdvisorBoundedLoop(prov, tools, _FakeDb())
    asyncio.run(loop.run_to_text_user(
        conversation_id="c", actor_user_id="u", customer_id="cc",
        context_user="x", system="s"))
    aaa.AdvisorBoundedLoop._audit_tool = orig
    assert len(audits) == AdvisorBoundedLoop.ToolRoundLimit
