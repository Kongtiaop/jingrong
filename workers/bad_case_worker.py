from __future__ import annotations

"""线上 bad case 落库 worker（数据飞轮"积累"自动落库）。

从 `logs/bad_cases.jsonl`（线上 BAD_CASE_COLLECT=1 时由 hook 写入）轮询新记录，
按 query_hash 去重后异步落库到 `bad_case` 表。实时开启：`python -m workers.bad_case_worker`。

用法：
  conda run -n langchain_env python -m workers.bad_case_worker
"""
import asyncio
import json
import os
import sys
from pathlib import Path

from app.common.logging import configure_logging
from app.common.logging.config import get_logger
from app.core.settings import get_settings
from app.db.session import Database
from app.repositories.bad_case_repository import BadCaseRepository

logger = get_logger(__name__)

BAD_CASES_FILE = Path(__file__).resolve().parents[1] / "logs" / "bad_cases.jsonl"
POLL_INTERVAL = 5.0


class BadCaseWorker:
    def __init__(self, database: Database, store_path=BAD_CASES_FILE) -> None:
        self.database = database
        self.store_path = Path(store_path)
        self.repo = BadCaseRepository(database)
        self._offset = 0  # 已消费的 JSONL 行号
        self._seen: set[str] = set()

    def _new_records(self) -> list[dict]:
        if not self.store_path.exists():
            return []
        out = []
        with open(self.store_path, encoding="utf-8") as f:
            for line_no, line in enumerate(f, start=1):
                if line_no <= self._offset:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                self._offset = line_no
                qh = rec.get("query_hash")
                if qh and qh not in self._seen:
                    self._seen.add(qh)
                    out.append(rec)
        return out

    async def run_once(self) -> int:
        inserted = 0
        for rec in self._new_records():
            ok = await self.repo.upsert_by_hash(
                customer_id=rec.get("customer_id"), query=rec.get("query", ""), query_hash=rec.get("query_hash", ""),
                intent=rec.get("intent", ""), confidence=rec.get("confidence"), evidence_status=rec.get("evidence_status", ""),
                handoff=bool(rec.get("handoff")), clarifications=rec.get("clarifications", 0), reasons=rec.get("reasons", []),
            )
            if ok:
                inserted += 1
        if inserted:
            logger.info("bad_case_worker inserted=%s", inserted)
        return inserted

    async def loop(self) -> None:
        logger.info("bad_case_worker started path=%s interval=%ss", self.store_path, POLL_INTERVAL)
        while True:
            try:
                await self.run_once()
            except Exception as exc:  # noqa: BLE001
                logger.exception("bad_case_worker cycle failed: %s", exc)
            await asyncio.sleep(POLL_INTERVAL)


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    database = Database(settings)
    worker = BadCaseWorker(database)
    try:
        asyncio.run(worker.loop())
    finally:
        asyncio.run(database.dispose())


if __name__ == "__main__":
    main()
