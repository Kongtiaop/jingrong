import json
import os
import shutil
import uuid

import pytest

from workers.bad_case_worker import BadCaseWorker


@pytest.fixture()
def tmp_workspace():
    path = os.path.join(os.getcwd(), "logs", "_bw_" + uuid.uuid4().hex[:8])
    os.makedirs(path, exist_ok=True)
    yield path
    shutil.rmtree(path, ignore_errors=True)


@pytest.fixture()
def store(tmp_workspace):
    return os.path.join(tmp_workspace, "bad_cases.jsonl")


def _append(path, records):
    with open(path, "a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def test_worker_incremental_read_and_dedup(store):
    _append(store, [
        {"query_hash": "h1", "query": "q1", "reasons": ["intent_unknown"]},
        {"query_hash": "h2", "query": "q2", "reasons": ["rag_low_confidence"]},
    ])
    w = BadCaseWorker(None, store_path=store)
    first = w._new_records()
    assert len(first) == 2 and w._offset == 2
    # 重复调用同一批 → 不再返回（offset 已推进 + seen 去重）
    assert w._new_records() == []
    # 追加新记录 → 只返回新的一条
    _append(store, [{"query_hash": "h3", "query": "q3", "reasons": []}])
    new = w._new_records()
    assert len(new) == 1 and new[0]["query_hash"] == "h3"
    # 同一 hash 去重（即使 offset 没推进）
    w2 = BadCaseWorker(None, store_path=store)
    all_recs = w2._new_records()
    hashes = [r["query_hash"] for r in all_recs]
    assert len(hashes) == len(set(hashes))
