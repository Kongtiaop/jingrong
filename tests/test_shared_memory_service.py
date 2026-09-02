"""多 Agent 共享记忆语义层：权限矩阵与校验（纯逻辑，不依赖 DB）。"""
import asyncio

import pytest

from app.services.shared_memory_service import SharedMemoryService, AGENT_MEMORY_PERMS


def test_permission_matrix_write_read():
    # 客服可读写 private+shared；data_analysis 只写 shared 只读 shared；business 只 private
    assert SharedMemoryService.can_access("customer_service", "shared", "write")
    assert SharedMemoryService.can_access("customer_service", "private", "read")
    assert SharedMemoryService.can_access("advisor", "shared", "read")
    assert SharedMemoryService.can_access("data_analysis", "shared", "write")
    assert not SharedMemoryService.can_access("data_analysis", "private", "write")
    assert not SharedMemoryService.can_access("business", "shared", "write")
    assert not SharedMemoryService.can_access("unknown_agent", "shared", "read")


def test_remember_rejects_unauthorized_write_before_db():
    # 权限校验在任何 DB 操作前，未授权作用域直接 PermissionError
    svc = SharedMemoryService.__new__(SharedMemoryService)  # 不构造 DB
    with pytest.raises(PermissionError):
        asyncio.run(svc.remember(customer_id="c1", agent_type="data_analysis", key="k", value="v", scope="private", source="t"))


def test_remember_rejects_invalid_scope_and_type():
    svc = SharedMemoryService.__new__(SharedMemoryService)
    with pytest.raises(ValueError):
        asyncio.run(svc.remember(customer_id="c1", agent_type="customer_service", key="k", value="v", scope="bad_scope", source="t"))
    with pytest.raises(ValueError):
        asyncio.run(svc.remember(customer_id="c1", agent_type="customer_service", key="k", value="v", scope="shared", memory_type="bad_type", source="t"))


def test_all_agents_have_defined_perms():
    for agent, perms in AGENT_MEMORY_PERMS.items():
        assert perms.get("write"), agent
        assert perms.get("read"), agent
        assert perms["write"] <= {"private", "shared", "organization"}
