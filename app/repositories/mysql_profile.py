from __future__ import annotations

from app.repositories.contracts import CustomerSummaryQueryRow

from typing import Any

import aiomysql


class MySQLRepository:
    def __init__(self, url: str):
        self.url = url
        self._pool = None

    def _params(self):
        raw = self.url.replace("mysql+aiomysql://", "").replace("mysql://", "")
        auth, rest = raw.rsplit("@", 1)
        user, password = auth.split(":", 1)
        hostdb, database = rest.split("/", 1)
        host, port = (hostdb.split(":") + ["3306"])[:2]
        return {"host": host, "port": int(port), "user": user, "password": password, "db": database, "autocommit": True}

    async def pool(self):
        if self._pool is None:
            self._pool = await aiomysql.create_pool(**self._params())
        return self._pool

    async def close(self):
        if self._pool is not None:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None

    async def fetch_one(self, sql: str, args: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        pool = await self.pool()
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, args)
                return await cur.fetchone()

    async def fetch_all(self, sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        pool = await self.pool()
        async with pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute(sql, args)
                return list(await cur.fetchall())

    async def execute(self, sql: str, args: tuple[Any, ...] = ()) -> int:
        pool = await self.pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, args)
                return cur.rowcount


class MySQLCustomerReadRepository:
    def __init__(self, database: MySQLRepository):
        self.database = database

    async def get_user(self, user_id: str):
        return await self.database.fetch_one("SELECT * FROM users WHERE id=%s", (user_id,))

    async def get_profile(self, user_id: str):
        return await self.database.fetch_one("SELECT * FROM customer_profile WHERE user_id=%s", (user_id,))


class MySQLProductReadRepository:
    def __init__(self, database: MySQLRepository):
        self.database = database

    async def get_active(self, product_id: str):
        return await self.database.fetch_one("SELECT * FROM product WHERE id=%s AND status='active' AND (deleted_at IS NULL)", (product_id,))

    async def list_active(self):
        return await self.database.fetch_all("SELECT * FROM product WHERE status='active' AND (deleted_at IS NULL) ORDER BY name")


class MySQLRiskAssessmentReadRepository:
    def __init__(self, database: MySQLRepository):
        self.database = database

    async def get_active(self, user_id: str):
        return await self.database.fetch_one("SELECT * FROM customer_risk_assessment WHERE user_id=%s AND status IN ('active','provisional') ORDER BY assessed_at DESC LIMIT 1", (user_id,))


class MySQLAccountReadRepository:
    def __init__(self, database: MySQLRepository):
        self.database = database

    async def get_by_user(self, user_id: str):
        return await self.database.fetch_one("SELECT * FROM account WHERE user_id=%s", (user_id,))


class MySQLOrderReadRepository:
    def __init__(self, database: MySQLRepository):
        self.database = database

    async def get(self, order_id: str, user_id: str | None = None):
        sql = "SELECT * FROM orders WHERE id=%s"
        args: tuple[Any, ...] = (order_id,)
        if user_id is not None:
            sql += " AND user_id=%s"
            args += (user_id,)
        return await self.database.fetch_one(sql, args)

    async def get_by_idempotency(self, user_id: str, key: str):
        return await self.database.fetch_one("SELECT * FROM orders WHERE user_id=%s AND idempotency_key=%s ORDER BY created_at DESC LIMIT 1", (user_id, key))

    async def list_for_user(self, user_id: str, limit: int | None = None, offset: int = 0):
        sql = "SELECT * FROM orders WHERE user_id=%s ORDER BY created_at DESC"
        args: list[Any] = [user_id]
        if limit is not None:
            sql += " LIMIT %s OFFSET %s"
            args.extend([limit, offset])
        return await self.database.fetch_all(sql, tuple(args))

    async def list_pending(self, limit: int = 20, offset: int = 0):
        total = await self.database.fetch_one("SELECT COUNT(*) AS n FROM orders WHERE status='pending_review'")
        rows = await self.database.fetch_all("SELECT * FROM orders WHERE status='pending_review' ORDER BY created_at LIMIT %s OFFSET %s", (limit, offset))
        return int(total["n"] if total else 0), rows


class MySQLTradeReadRepository:
    def __init__(self, database: MySQLRepository):
        self.database = database

    async def list_for_user(self, user_id: str):
        return await self.database.fetch_all("SELECT * FROM trades WHERE user_id=%s ORDER BY executed_at DESC", (user_id,))


class MySQLStatusHistoryReadRepository:
    def __init__(self, database: MySQLRepository):
        self.database = database

    async def list_for_order(self, order_id: str):
        return await self.database.fetch_all("SELECT * FROM order_status_history WHERE order_id=%s ORDER BY created_at", (order_id,))


class MySQLAssetHoldingReadRepository:
    def __init__(self, database: MySQLRepository):
        self.database = database

    async def latest_asset(self, user_id: str):
        return await self.database.fetch_one("SELECT * FROM customer_asset_snapshot WHERE user_id=%s ORDER BY snapshot_time DESC, created_at DESC, id DESC LIMIT 1", (user_id,))

    async def active_holdings(self, user_id: str):
        return await self.database.fetch_all("SELECT * FROM customer_holding WHERE user_id=%s AND status='active' ORDER BY product_id", (user_id,))


class MySQLTransaction:
    """Explicit transaction scope for isolated MySQL write tests."""
    def __init__(self, database: MySQLRepository):
        self.database = database
        self.connection = None

    async def __aenter__(self):
        pool = await self.database.pool()
        self.connection = await pool.acquire()
        await self.connection.begin()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self.connection is None:
            return False
        try:
            if exc_type is None:
                await self.connection.commit()
            else:
                await self.connection.rollback()
        finally:
            pool = self.database._pool
            if pool is not None:
                pool.release(self.connection)
            self.connection = None
        return False

    async def fetch_one(self, sql: str, args: tuple[Any, ...] = ()):
        async with self.connection.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(sql, args)
            return await cur.fetchone()

    async def fetch_all(self, sql: str, args: tuple[Any, ...] = ()):
        async with self.connection.cursor(aiomysql.DictCursor) as cur:
            await cur.execute(sql, args)
            return list(await cur.fetchall())

    async def execute(self, sql: str, args: tuple[Any, ...] = ()) -> int:
        async with self.connection.cursor() as cur:
            await cur.execute(sql, args)
            return cur.rowcount

    async def insert_order(self, values: dict[str, Any]) -> None:
        columns = ["id", "order_no", "user_id", "account_id", "product_id", "amount", "quantity", "status", "side", "review_note", "failure_reason", "idempotency_key"]
        await self.execute(
            f"INSERT INTO orders ({','.join(columns)}) VALUES ({','.join(['%s'] * len(columns))})",
            tuple(values.get(column) for column in columns),
        )

    async def transition_order(self, order_id: str, from_status: str, to_status: str, operator_id: str, note: str = "") -> None:
        await self.execute("UPDATE orders SET status=%s,review_note=%s,updated_at=NOW() WHERE id=%s AND status=%s", (to_status, note, order_id, from_status))
        await self.execute("INSERT INTO order_status_history(id,order_id,from_status,to_status,operator_user_id,note) VALUES(UUID(),%s,%s,%s,%s,%s)", (order_id, from_status, to_status, operator_id, note))

    async def lock_account(self, account_id: str):
        return await self.fetch_one("SELECT * FROM account WHERE id=%s FOR UPDATE", (account_id,))

    async def update_account(self, account_id: str, available_balance, frozen_balance) -> None:
        await self.execute("UPDATE account SET available_balance=%s,frozen_balance=%s,updated_at=NOW() WHERE id=%s", (available_balance, frozen_balance, account_id))

    async def insert_trade(self, values: dict[str, Any]) -> None:
        await self.execute("INSERT INTO trades(id,trade_no,order_id,user_id,product_id,amount,quantity) VALUES(%s,%s,%s,%s,%s,%s,%s)", tuple(values[key] for key in ("id","trade_no","order_id","user_id","product_id","amount","quantity")))


from types import SimpleNamespace

class MySQLRow(SimpleNamespace):
    @classmethod
    def from_mapping(cls, row):
        return None if row is None else cls(**row)

class MySQLServiceReadAdapter:
    def __init__(self, database): self.database=database
    def _source(self, session): return session if isinstance(session, MySQLTransaction) else self.database


class MySQLCustomerQueryAdapter(MySQLServiceReadAdapter):
    async def list_staff_customer_views(self, session, q="", limit=50, offset=0):
        source=self._source(session); where=["u.status='active'"]; args=[]
        if q.strip(): where.append("(u.username LIKE %s OR u.display_name LIKE %s)"); needle=f"%{q.strip()}%"; args.extend([needle,needle])
        clause=" WHERE "+" AND ".join(where)
        users=await source.fetch_all("SELECT u.* FROM users u"+clause+" ORDER BY u.created_at LIMIT %s OFFSET %s",tuple(args+[limit,offset]))
        ids=[u['id'] for u in users]
        if not ids: return []
        marks=','.join(['%s']*len(ids))
        profiles={r['user_id']:MySQLRow.from_mapping(r) for r in await source.fetch_all("SELECT * FROM customer_profile WHERE user_id IN ("+marks+")",tuple(ids))}
        risks={}
        for r in await source.fetch_all("SELECT * FROM customer_risk_assessment WHERE user_id IN ("+marks+") AND status IN ('active','provisional') ORDER BY assessed_at DESC",tuple(ids)): risks.setdefault(r['user_id'],MySQLRow.from_mapping(r))
        counts={r['user_id']:int(r['n']) for r in await source.fetch_all("SELECT user_id,COUNT(*) AS n FROM customer_holding WHERE user_id IN ("+marks+") AND status='active' GROUP BY user_id",tuple(ids))}
        assets={}
        for r in await source.fetch_all("SELECT * FROM customer_asset_snapshot WHERE user_id IN ("+marks+") ORDER BY snapshot_time DESC,created_at DESC",tuple(ids)): assets.setdefault(r['user_id'],MySQLRow.from_mapping(r))
        return [CustomerSummaryQueryRow(user=MySQLRow.from_mapping(u), profile=profiles.get(u['id']), risk=risks.get(u['id']), holding_count=counts.get(u['id'],0), asset=assets.get(u['id'])) for u in users]

class MySQLUsersAdapter(MySQLServiceReadAdapter):
    async def get(self, session, user_id): return MySQLRow.from_mapping(await self._source(session).fetch_one("SELECT * FROM users WHERE id=%s",(user_id,)))
    async def is_customer(self, session, user_id): return await self._source(session).fetch_one("SELECT 1 AS ok FROM customer_profile WHERE user_id=%s",(user_id,)) is not None

class MySQLProfilesAdapter(MySQLServiceReadAdapter):
    async def get(self, session, user_id): return MySQLRow.from_mapping(await self._source(session).fetch_one("SELECT * FROM customer_profile WHERE user_id=%s",(user_id,)))
    async def list_customers(self, session, q="", limit=50, offset=0):
        source=self._source(session); where=["u.status='active'"]; args=[]
        if q.strip(): where.append("(u.username LIKE %s OR u.display_name LIKE %s)"); needle=f"%{q.strip()}%"; args.extend([needle,needle])
        clause=" WHERE "+" AND ".join(where)
        total_row=await source.fetch_one("SELECT COUNT(*) AS n FROM users u"+clause,tuple(args))
        rows=await source.fetch_all("SELECT u.* FROM users u"+clause+" ORDER BY u.created_at LIMIT %s OFFSET %s",tuple(args+[limit,offset]))
        return int(total_row["n"] if total_row else 0), [MySQLRow.from_mapping(row) for row in rows]
    async def save(self, session, profile):
        source = self._source(session)
        await _mysql_insert_record(source, "customer_profile", profile, [
            "id", "user_id", "age", "occupation", "region", "customer_type",
            "customer_tier", "investment_experience_years", "investment_goal",
            "investment_horizon_years", "liquidity_preference", "source_type",
        ])
        return await self.get(session, profile.user_id)
    async def update_values(self, session, profile):
        source = self._source(session)
        await source.execute(
            "UPDATE customer_profile SET age=%s,occupation=%s,region=%s,customer_type=%s,customer_tier=%s,investment_experience_years=%s,investment_goal=%s,investment_horizon_years=%s,liquidity_preference=%s,source_type=%s,updated_at=NOW() WHERE id=%s",
            (profile.age, profile.occupation, profile.region, profile.customer_type, profile.customer_tier, profile.investment_experience_years, profile.investment_goal, profile.investment_horizon_years, profile.liquidity_preference, profile.source_type, profile.id),
        )
        return await self.get(session, profile.user_id)

class MySQLAuditLogAdapter(MySQLServiceReadAdapter):
    async def save(self, session, log):
        source = self._source(session)
        await _mysql_insert_record(source, "audit_logs", log, [
            "id", "actor_user_id", "action", "resource_type", "resource_id", "detail",
        ])
        return log

    async def list(self, session, action="", resource_type="", limit=50, offset=0):
        source = self._source(session)
        where=[]; args=[]
        if action.strip(): where.append("action=%s"); args.append(action.strip())
        if resource_type.strip(): where.append("resource_type=%s"); args.append(resource_type.strip())
        clause=(" WHERE "+" AND ".join(where)) if where else ""
        total_row=await source.fetch_one("SELECT COUNT(*) AS n FROM audit_logs"+clause, tuple(args))
        rows=await source.fetch_all("SELECT * FROM audit_logs"+clause+" ORDER BY created_at DESC LIMIT %s OFFSET %s", tuple(args+[limit,offset]))
        return int(total_row["n"] if total_row else 0), [MySQLRow.from_mapping(row) for row in rows]

    async def list_for_resource(self, session, resource_type, resource_id, limit=100):
        rows = await self._source(session).fetch_all(
            "SELECT * FROM audit_logs WHERE resource_type=%s AND resource_id=%s ORDER BY created_at DESC LIMIT %s",
            (resource_type, resource_id, limit),
        )
        return [MySQLRow.from_mapping(row) for row in rows]


class MySQLEnterpriseVerificationAdapter(MySQLServiceReadAdapter):
    async def get(self, session, user_id):
        return MySQLRow.from_mapping(await self._source(session).fetch_one(
            "SELECT * FROM customer_enterprise_verification WHERE user_id=%s", (user_id,)
        ))

    async def get_by_id(self, session, verification_id):
        return MySQLRow.from_mapping(await self._source(session).fetch_one("SELECT * FROM customer_enterprise_verification WHERE id=%s", (verification_id,)))

    async def list(self, session, status="pending"):
        sql="SELECT * FROM customer_enterprise_verification"; args=[]
        if status in {"pending","approved","rejected"}: sql += " WHERE status=%s"; args.append(status)
        sql += " ORDER BY created_at DESC"
        return [MySQLRow.from_mapping(row) for row in await self._source(session).fetch_all(sql, tuple(args))]

    async def save(self, session, verification):
        source = self._source(session)
        await _mysql_insert_record(source, "customer_enterprise_verification", verification, [
            "id", "user_id", "company_name", "registration_no", "legal_representative",
            "contact_phone", "status", "review_note", "reviewed_by", "reviewed_at",
        ])
        return await self.get(session, verification.user_id)

    async def update_values(self, session, verification):
        source = self._source(session)
        await source.execute(
            "UPDATE customer_enterprise_verification SET company_name=%s,registration_no=%s,legal_representative=%s,contact_phone=%s,status=%s,review_note=%s,reviewed_by=%s,reviewed_at=%s,updated_at=NOW() WHERE id=%s",
            (verification.company_name, verification.registration_no, verification.legal_representative, verification.contact_phone, verification.status, verification.review_note, verification.reviewed_by, verification.reviewed_at, verification.id),
        )
        return await self.get(session, verification.user_id)


class MySQLRisksAdapter(MySQLServiceReadAdapter):
    async def get_active(self, session, user_id):
        return MySQLRow.from_mapping(await self._source(session).fetch_one("SELECT * FROM customer_risk_assessment WHERE user_id=%s AND status IN ('active','provisional') ORDER BY assessed_at DESC LIMIT 1", (user_id,)))

    async def supersede_active(self, session, user_id):
        await self._source(session).execute(
            "UPDATE customer_risk_assessment SET status='superseded',updated_at=NOW() WHERE user_id=%s AND status='active'",
            (user_id,),
        )

    async def save(self, session, assessment):
        source = self._source(session)
        await _mysql_insert_record(
            source,
            "customer_risk_assessment",
            assessment,
            ["id", "user_id", "risk_level", "score", "answers_json", "status", "source_type", "assessed_at", "expires_at"],
        )
        return assessment

    async def get_latest_active(self, session, user_id):
        return await self.get_active(session, user_id)

class MySQLProductsAdapter(MySQLServiceReadAdapter):
    async def get_by_id(self, session, product_id): return MySQLRow.from_mapping(await self._source(session).fetch_one("SELECT * FROM product WHERE id=%s",(product_id,)))
    async def get_active(self, session, product_id): return MySQLRow.from_mapping(await self._source(session).fetch_one("SELECT * FROM product WHERE id=%s AND status='active' AND deleted_at IS NULL",(product_id,)))
    async def list_active(self, session): return [MySQLRow.from_mapping(row) for row in await self._source(session).fetch_all("SELECT * FROM product WHERE status='active' AND deleted_at IS NULL ORDER BY name")]
    async def save(self, session, product):
        return await _mysql_insert_record(self._source(session), "product", product, ["id","name","product_type","risk_level","term_days","minimum_amount","liquidity","description","target_customer_type","status","source_type"])
    async def update_values(self, session, product):
        await self._source(session).execute("UPDATE product SET name=%s,product_type=%s,risk_level=%s,term_days=%s,minimum_amount=%s,liquidity=%s,description=%s,target_customer_type=%s,status=%s,source_type=%s,deleted_at=%s,updated_at=NOW() WHERE id=%s", (product.name,product.product_type,product.risk_level,product.term_days,product.minimum_amount,product.liquidity,product.description,product.target_customer_type,product.status,product.source_type,product.deleted_at,product.id))
        return await self.get_by_id(session, product.id)

class MySQLAccountsAdapter(MySQLServiceReadAdapter):
    async def save(self, session, record):
        source=self._source(session); return await _mysql_insert_record(source, "account", record, ["id","user_id","account_no","currency","available_balance","frozen_balance","status"])
    async def update_balances(self, session, account_id, available_balance, frozen_balance):
        source=self._source(session); await source.execute("UPDATE account SET available_balance=%s,frozen_balance=%s,updated_at=NOW() WHERE id=%s",(available_balance,frozen_balance,account_id)); return await self.get_by_id(session,account_id)
    async def create_default(self, session, user_id: str, account_no: str, available_balance):
        source=self._source(session)
        await source.execute("INSERT INTO account(id,user_id,account_no,currency,available_balance,frozen_balance,status) VALUES(UUID(),%s,%s,'CNY',%s,0,'active')", (user_id, account_no, available_balance))
        return await self.get_by_user(session, user_id)
    async def get_by_user(self, session, user_id, for_update=False):
        return MySQLRow.from_mapping(await self._source(session).fetch_one("SELECT * FROM account WHERE user_id=%s"+(" FOR UPDATE" if for_update else ""),(user_id,)))
    async def get_by_id(self, session, account_id, for_update=False):
        return MySQLRow.from_mapping(await self._source(session).fetch_one("SELECT * FROM account WHERE id=%s"+(" FOR UPDATE" if for_update else ""),(account_id,)))

class MySQLOrdersAdapter(MySQLServiceReadAdapter):
    async def save(self, session, record):
        source=self._source(session); return await _mysql_insert_record(source, "orders", record, ["id","order_no","user_id","account_id","product_id","amount","quantity","status","side","review_note","failure_reason","idempotency_key"])
    async def update_status(self, session, order_id, status, review_note="", failure_reason=""):
        source=self._source(session); await source.execute("UPDATE orders SET status=%s,review_note=%s,failure_reason=%s,updated_at=NOW() WHERE id=%s",(status,review_note,failure_reason,order_id)); return await self.get_for_update(session,order_id)
    async def get_by_idempotency(self, session,user_id,key): return MySQLRow.from_mapping(await self._source(session).fetch_one("SELECT * FROM orders WHERE user_id=%s AND idempotency_key=%s ORDER BY created_at DESC LIMIT 1",(user_id,key)))
    async def get_for_user(self,session,order_id,user_id,for_update=False): return MySQLRow.from_mapping(await self._source(session).fetch_one("SELECT * FROM orders WHERE id=%s AND user_id=%s"+(" FOR UPDATE" if for_update else ""),(order_id,user_id)))
    async def get_for_update(self,session,order_id): return MySQLRow.from_mapping(await self._source(session).fetch_one("SELECT * FROM orders WHERE id=%s FOR UPDATE",(order_id,)))

class MySQLHoldingsAdapter(MySQLServiceReadAdapter):
    async def list_active(self, session, user_id):
        return [MySQLRow.from_mapping(row) for row in await self._source(session).fetch_all(
            "SELECT * FROM customer_holding WHERE user_id=%s AND status='active' ORDER BY product_id", (user_id,)
        )]
    async def count_active(self, session, user_id):
        row=await self._source(session).fetch_one("SELECT COUNT(*) AS n FROM customer_holding WHERE user_id=%s AND status='active'",(user_id,))
        return int(row['n'] if row else 0)

    async def save(self, session, record):
        source=self._source(session); return await _mysql_insert_record(source, "customer_holding", record, ["id","user_id","product_id","quantity","cost_amount","market_value","profit_loss","holding_days","status"])
    async def update_values(self, session, record):
        source=self._source(session); await source.execute("UPDATE customer_holding SET quantity=%s,cost_amount=%s,market_value=%s,profit_loss=%s,holding_days=%s,status=%s,updated_at=NOW() WHERE id=%s",(record.quantity,record.cost_amount,record.market_value,record.profit_loss,record.holding_days,record.status,record.id)); return record
    async def get_active(self,session,user_id,product_id,for_update=False): return MySQLRow.from_mapping(await self._source(session).fetch_one("SELECT * FROM customer_holding WHERE user_id=%s AND product_id=%s AND status='active'"+(" FOR UPDATE" if for_update else ""),(user_id,product_id)))

class MySQLTradesAdapter(MySQLServiceReadAdapter):
    async def save(self, session, record):
        source=self._source(session); return await _mysql_insert_record(source, "trades", record, ["id","trade_no","order_id","user_id","product_id","amount","quantity"])
    async def list_for_user(self,session,user_id): return [MySQLRow.from_mapping(x) for x in await self._source(session).fetch_all("SELECT * FROM trades WHERE user_id=%s ORDER BY executed_at DESC",(user_id,))]

class MySQLStatusHistoryAdapter(MySQLServiceReadAdapter):
    async def save(self, session, record):
        source=self._source(session); return await _mysql_insert_record(source, "order_status_history", record, ["id","order_id","from_status","to_status","operator_user_id","note"])
    async def list_for_order(self,session,order_id): return [MySQLRow.from_mapping(x) for x in await self._source(session).fetch_all("SELECT * FROM order_status_history WHERE order_id=%s ORDER BY created_at",(order_id,))]


async def _mysql_insert_record(source, table, record, columns):
    values=[getattr(record, column, None) for column in columns]
    await source.execute(f"INSERT INTO `{table}` ({','.join(columns)}) VALUES ({','.join(['%s']*len(columns))})", tuple(values))
    return record



class MySQLAssetsAdapter(MySQLServiceReadAdapter):
    async def latest(self, session, user_id):
        return MySQLRow.from_mapping(await self._source(session).fetch_one("SELECT * FROM customer_asset_snapshot WHERE user_id=%s ORDER BY snapshot_time DESC,created_at DESC,id DESC LIMIT 1", (user_id,)))
    async def save(self, session, record):
        source=self._source(session)
        await _mysql_insert_record(source, "customer_asset_snapshot", record, ["id","user_id","total_asset","cash_balance","investable_asset","liability","net_asset","source_type"])
        return record
    async def update_values(self, session, record):
        source=self._source(session)
        await source.execute("UPDATE customer_asset_snapshot SET total_asset=%s,cash_balance=%s,investable_asset=%s,liability=%s,net_asset=%s,source_type=%s WHERE id=%s", (record.total_asset,record.cash_balance,record.investable_asset,record.liability,record.net_asset,record.source_type,record.id))
        return record
