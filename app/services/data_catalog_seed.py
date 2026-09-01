from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select

from app.db.session import Database
from app.models.data_catalog import (
    DataCatalogColumn,
    DataCatalogDomain,
    DataCatalogExampleQuery,
    DataCatalogRelation,
    DataCatalogTable,
)


CORE_CATALOG = {
    "domains": [
        {
            "code": "customer_profile",
            "name": "客户画像与资产",
            "description": "客户身份、画像、风险偏好、资产快照和持仓等客户经营数据。",
            "permission_level": "restricted",
            "allowed_roles_json": ["advisor", "customer_manager", "risk_manager", "operations", "auditor", "super_admin"],
        },
        {
            "code": "trading",
            "name": "交易与账户",
            "description": "交易账户、订单、成交和资金流水等交易事实数据。",
            "permission_level": "restricted",
            "allowed_roles_json": ["advisor", "operations", "risk_manager", "auditor", "super_admin"],
        },
        {
            "code": "product",
            "name": "产品与适当性",
            "description": "理财产品、适当性规则和产品风险规则等产品治理数据。",
            "permission_level": "internal",
            "allowed_roles_json": ["advisor", "product_manager", "operations", "risk_manager", "auditor", "super_admin"],
        },
        {
            "code": "risk",
            "name": "风险与合规",
            "description": "风险预警、AML 批次和风险规则等风险控制数据。",
            "permission_level": "restricted",
            "allowed_roles_json": ["risk_manager", "auditor", "super_admin"],
        },
        {
            "code": "service_operations",
            "name": "服务工单与运营",
            "description": "客户服务工单、通知、异步任务和审计等运营过程数据。",
            "permission_level": "internal",
            "allowed_roles_json": ["customer_service", "customer_manager", "operations", "risk_manager", "auditor", "super_admin"],
        },
    ],
    "tables": [
        {
            "domain_code": "customer_profile",
            "table_name": "users",
            "display_name": "用户主数据",
            "description": "系统登录用户、客户和员工的统一身份主数据。",
            "business_purpose": "用于识别客户或员工、关联角色、账户、画像、订单和工单。",
            "grain_description": "一行代表一个系统用户；客户和员工共用该身份表。",
            "usage_guidance": "需要客户姓名/账号或跨业务表关联主体时使用。",
            "non_usage_guidance": "不保存资产、持仓、订单金额或风险评估结果。",
            "table_kind": "dimension",
            "permission_level": "restricted",
            "allowed_roles_json": ["advisor", "customer_manager", "risk_manager", "operations", "auditor", "super_admin"],
            "aliases_json": ["用户", "客户", "员工", "客户账号", "登录账号"],
            "is_queryable": True,
            "columns": [
                ("id", "用户 ID", "用户的稳定主键，用于跨表关联。", "identifier", "", True, False, True, False, ["用户编号", "客户ID"], []),
                ("username", "登录账号", "用户登录名；演示环境常用于定位客户。", "identifier", "", False, False, True, False, ["账号", "用户名", "客户账号"], []),
                ("display_name", "显示名称", "界面显示的客户或员工名称。", "person_name", "", False, False, True, True, ["姓名", "客户姓名", "昵称"], []),
                ("status", "用户状态", "账户是否处于 active 等可用状态。", "status", "", False, False, True, False, ["账户状态", "客户状态"], ["active"]),
            ],
        },
        {
            "domain_code": "customer_profile",
            "table_name": "customer_profile",
            "display_name": "客户画像",
            "description": "客户静态画像、投资经验、目标、期限和流动性偏好。",
            "business_purpose": "用于适当性、客户分层和投顾服务；不保存历史资产金额。",
            "grain_description": "一行代表一个客户当前有效画像。",
            "usage_guidance": "查询客户投资目标、客户等级、经验或偏好时使用。",
            "non_usage_guidance": "查询资产趋势、账户余额或成交记录时不要使用。",
            "table_kind": "dimension",
            "permission_level": "restricted",
            "allowed_roles_json": ["advisor", "customer_manager", "risk_manager", "operations", "auditor", "super_admin"],
            "aliases_json": ["客户画像", "投资偏好", "客户等级", "投资目标"],
            "is_queryable": True,
            "columns": [
                ("user_id", "客户 ID", "关联 users.id 的客户身份。", "identifier", "", False, True, True, False, ["客户", "用户ID"], []),
                ("customer_tier", "客户等级", "普通、高净值等客户经营分层。", "category", "", False, False, True, False, ["高净值", "客户层级"], ["ordinary", "high_net_worth"]),
                ("investment_goal", "投资目标", "客户偏好的投资目标，如稳健增值等。", "category", "", False, False, True, False, ["理财目标", "投资目的"], []),
                ("investment_horizon_years", "投资期限（年）", "客户可接受的投资期限年数。", "number", "year", True, False, True, False, ["投资年限", "期限"], []),
                ("liquidity_preference", "流动性偏好", "客户对资金可用性的偏好。", "category", "", False, False, True, False, ["流动性", "赎回偏好"], ["low", "medium", "high"]),
            ],
        },
        {
            "domain_code": "customer_profile",
            "table_name": "customer_asset_snapshot",
            "display_name": "客户资产每日快照",
            "description": "客户按估值日期保存的总资产、现金、投资资产、负债和净资产历史快照。",
            "business_purpose": "用于资产趋势、净资产变化、客户资产规模和每日估值分析。",
            "grain_description": "一行代表一个客户在一个 snapshot_date 的资产估值快照。",
            "usage_guidance": "查询资产趋势、历史净资产、某日资产规模或资产增长时使用。",
            "non_usage_guidance": "不表示实时订单状态；不能代替 customer_profile 的投资偏好。",
            "table_kind": "snapshot",
            "permission_level": "restricted",
            "allowed_roles_json": ["advisor", "customer_manager", "risk_manager", "operations", "auditor", "super_admin"],
            "aliases_json": ["资产快照", "资产趋势", "净资产", "客户资产", "每日估值"],
            "is_queryable": True,
            "columns": [
                ("user_id", "客户 ID", "关联 users.id 的客户身份。", "identifier", "", False, True, True, False, ["客户", "用户ID"], []),
                ("snapshot_date", "快照日期", "资产估值所属业务日期。", "date", "day", False, False, True, False, ["估值日", "日期", "资产日期"], []),
                ("total_asset", "总资产", "客户资产总额。", "money", "CNY", False, False, True, True, ["资产总额", "总资产金额"], []),
                ("cash_balance", "现金余额", "客户可用现金资产。", "money", "CNY", False, False, True, True, ["现金", "现金资产"], []),
                ("investment_asset", "投资资产", "客户投资类资产金额。", "money", "CNY", False, False, True, True, ["理财资产", "投资金额"], []),
                ("liability", "负债", "客户负债金额。", "money", "CNY", False, False, True, True, ["负债金额", "欠款"], []),
                ("net_asset", "净资产", "总资产扣除负债后的净资产金额。", "money", "CNY", False, False, True, True, ["资产净值", "净资产金额"], []),
            ],
        },
        {
            "domain_code": "customer_profile",
            "table_name": "customer_holding",
            "display_name": "客户持仓",
            "description": "客户在账户和产品维度的当前持仓份额、成本和估值信息。",
            "business_purpose": "用于查询客户当前持有的产品、持仓市值和浮动收益。",
            "grain_description": "一行代表一个客户账户对一个产品的当前持仓。",
            "usage_guidance": "查询当前持仓、某产品持有情况或持仓盈亏时使用。",
            "non_usage_guidance": "不用于历史资产趋势；历史趋势应查询 customer_asset_snapshot。",
            "table_kind": "snapshot",
            "permission_level": "restricted",
            "allowed_roles_json": ["advisor", "customer_manager", "risk_manager", "operations", "auditor", "super_admin"],
            "aliases_json": ["持仓", "当前持有", "基金持仓", "盈亏"],
            "is_queryable": True,
            "columns": [
                ("user_id", "客户 ID", "关联 users.id 的客户身份。", "identifier", "", False, True, True, False, ["客户", "用户ID"], []),
                ("account_id", "账户 ID", "关联 account.id 的交易账户。", "identifier", "", False, True, True, False, ["交易账户", "账户"], []),
                ("product_id", "产品 ID", "关联 product.id 的产品。", "identifier", "", False, True, True, False, ["理财产品", "产品"], []),
                ("market_value", "持仓市值", "当前持仓按估值计算的市场价值。", "money", "CNY", False, False, True, True, ["市值", "持仓金额"], []),
                ("unrealized_pnl", "浮动盈亏", "当前持仓未实现收益或损失。", "money", "CNY", False, False, True, True, ["浮盈", "浮亏", "持仓收益"], []),
            ],
        },
        {
            "domain_code": "trading",
            "table_name": "account",
            "display_name": "交易账户",
            "description": "客户用于交易、扣款和持仓归集的账户。",
            "business_purpose": "连接客户、订单、成交、资金流水和持仓。",
            "grain_description": "一行代表一个客户交易账户。",
            "usage_guidance": "查询客户账户、账户状态或通过账户关联订单/资金流水时使用。",
            "non_usage_guidance": "不直接保存每笔订单或每日资产快照。",
            "table_kind": "entity",
            "permission_level": "restricted",
            "allowed_roles_json": ["advisor", "operations", "risk_manager", "auditor", "super_admin"],
            "aliases_json": ["账户", "交易账户", "资金账户"],
            "is_queryable": True,
            "columns": [
                ("id", "账户 ID", "交易账户主键。", "identifier", "", True, False, True, False, ["账户编号"], []),
                ("user_id", "客户 ID", "账户所属客户，关联 users.id。", "identifier", "", False, True, True, False, ["账户客户", "用户ID"], []),
                ("account_no", "账户编号", "业务展示的交易账户编号。", "identifier", "", False, False, True, True, ["资金账号", "交易账号"], []),
                ("status", "账户状态", "交易账户是否可用、冻结或受限制。", "status", "", False, False, True, False, ["账户状态", "冻结状态"], []),
            ],
        },
        {
            "domain_code": "trading",
            "table_name": "orders",
            "display_name": "交易订单",
            "description": "客户申购、赎回等交易请求及其审核和执行状态。",
            "business_purpose": "用于查询订单金额、类型、状态、发起时间和处理进度。",
            "grain_description": "一行代表一笔客户交易订单。",
            "usage_guidance": "查询订单状态、订单金额、订单数量或某期间订单时使用。",
            "non_usage_guidance": "成交结果和实际成交时间应优先查询 trades；不要用订单替代资产快照。",
            "table_kind": "event",
            "permission_level": "restricted",
            "allowed_roles_json": ["advisor", "operations", "risk_manager", "auditor", "super_admin"],
            "aliases_json": ["订单", "申购", "赎回", "交易申请", "待审核订单"],
            "is_queryable": True,
            "columns": [
                ("id", "订单 ID", "订单主键。", "identifier", "", True, False, True, False, ["订单编号"], []),
                ("user_id", "客户 ID", "下单客户，关联 users.id。", "identifier", "", False, True, True, False, ["客户", "用户ID"], []),
                ("account_id", "账户 ID", "订单使用的交易账户，关联 account.id。", "identifier", "", False, True, True, False, ["交易账户"], []),
                ("product_id", "产品 ID", "订单对应的产品，关联 product.id。", "identifier", "", False, True, True, False, ["理财产品"], []),
                ("order_type", "订单类型", "申购、赎回等订单业务类型。", "category", "", False, False, True, False, ["交易类型", "买入卖出"], []),
                ("amount", "订单金额", "客户申请的交易金额，不等于最终成交金额。", "money", "CNY", False, False, True, True, ["申购金额", "赎回金额", "下单金额"], []),
                ("status", "订单状态", "订单当前审核、执行或终止状态。", "status", "", False, False, True, False, ["审核状态", "订单进度"], []),
                ("created_at", "创建时间", "订单发起时间。", "datetime", "", False, False, True, False, ["下单时间", "申请时间"], []),
            ],
        },
        {
            "domain_code": "trading",
            "table_name": "trades",
            "display_name": "交易成交记录",
            "description": "订单实际成交后的金额、份额、价格和成交时间事实记录。",
            "business_purpose": "用于成交金额、成交数量、执行结果和订单完成情况分析。",
            "grain_description": "一行代表一次实际成交执行记录。",
            "usage_guidance": "查询最终成交、成交金额或成交时间时使用。",
            "non_usage_guidance": "下单申请与审核状态应查询 orders。",
            "table_kind": "event",
            "permission_level": "restricted",
            "allowed_roles_json": ["advisor", "operations", "risk_manager", "auditor", "super_admin"],
            "aliases_json": ["成交", "交易完成", "成交记录", "成交金额"],
            "is_queryable": True,
            "columns": [
                ("order_id", "订单 ID", "成交所属订单，关联 orders.id。", "identifier", "", False, True, True, False, ["订单", "订单编号"], []),
                ("amount", "成交金额", "实际成交金额。", "money", "CNY", False, False, True, True, ["成交额", "实际金额"], []),
                ("units", "成交份额", "实际成交的产品份额。", "number", "share", False, False, True, True, ["份额", "成交数量"], []),
                ("trade_price", "成交价格", "实际成交价格。", "money", "CNY", False, False, True, True, ["价格", "成交单价"], []),
                ("executed_at", "成交时间", "成交执行完成时间。", "datetime", "", False, False, True, False, ["成交日期", "执行时间"], []),
            ],
        },
        {
            "domain_code": "product",
            "table_name": "product",
            "display_name": "理财产品",
            "description": "可售理财产品的类型、风险等级、最低金额、流动性和状态。",
            "business_purpose": "用于产品查询、适当性校验和推荐候选筛选。",
            "grain_description": "一行代表一个可管理的理财产品。",
            "usage_guidance": "查询产品名称、风险等级、状态、最低起投金额或流动性时使用。",
            "non_usage_guidance": "是否适合某客户仍需结合 customer_profile、customer_risk_assessment 和 product_suitability_rule。",
            "table_kind": "dimension",
            "permission_level": "internal",
            "allowed_roles_json": ["advisor", "product_manager", "operations", "risk_manager", "auditor", "super_admin"],
            "aliases_json": ["产品", "理财产品", "基金", "可售产品"],
            "is_queryable": True,
            "columns": [
                ("id", "产品 ID", "产品主键。", "identifier", "", True, False, True, False, ["产品编号"], []),
                ("name", "产品名称", "产品展示名称。", "text", "", False, False, True, False, ["理财名称", "基金名称"], []),
                ("product_type", "产品类型", "基金、存款、理财等产品分类。", "category", "", False, False, True, False, ["产品类别", "理财类型"], []),
                ("risk_level", "产品风险等级", "产品风险等级。", "risk_level", "", False, False, True, False, ["风险级别", "产品风险"], ["C1", "C2", "C3", "C4", "C5"]),
                ("minimum_amount", "最低起投金额", "购买该产品要求的最低金额。", "money", "CNY", False, False, True, True, ["起投金额", "最低购买金额"], []),
                ("status", "产品状态", "产品是否处于 active 等可售状态。", "status", "", False, False, True, False, ["在售状态", "产品是否可买"], []),
            ],
        },
        {
            "domain_code": "risk",
            "table_name": "risk_alert",
            "display_name": "风险预警",
            "description": "客户交易和资金行为触发的风险预警及其调查、处置和限制状态。",
            "business_purpose": "用于风控排查、订单门禁、调查进度和风险处置分析。",
            "grain_description": "一行代表一条针对客户/业务对象的风险预警。",
            "usage_guidance": "查询风险预警、未结案风险、预警级别或处置状态时使用。",
            "non_usage_guidance": "不直接代替订单审核结论或客户资产趋势。",
            "table_kind": "event",
            "permission_level": "restricted",
            "allowed_roles_json": ["risk_manager", "auditor", "super_admin"],
            "aliases_json": ["风险预警", "风控告警", "风险事件", "AML 告警"],
            "is_queryable": True,
            "columns": [
                ("user_id", "客户 ID", "被预警客户，关联 users.id。", "identifier", "", False, True, True, False, ["客户", "用户ID"], []),
                ("rule_code", "规则编码", "触发预警的风险规则编码。", "identifier", "", False, False, True, False, ["风控规则", "AML规则"], []),
                ("risk_level", "风险等级", "预警风险等级。", "risk_level", "", False, False, True, False, ["告警等级", "风险级别"], []),
                ("status", "处置状态", "预警调查、待补件、结案等处理状态。", "status", "", False, False, True, False, ["预警状态", "调查状态"], []),
                ("created_at", "创建时间", "风险预警产生时间。", "datetime", "", False, False, True, False, ["预警时间", "告警时间"], []),
            ],
        },
        {
            "domain_code": "service_operations",
            "table_name": "work_order",
            "display_name": "服务工单",
            "description": "客户服务、风险调查和补件等人工处理任务。",
            "business_purpose": "用于查询客户工单、分派状态、处理进度和解决记录。",
            "grain_description": "一行代表一张服务工单。",
            "usage_guidance": "查询客服转人工、风险补件、工单处理人或工单状态时使用。",
            "non_usage_guidance": "不作为订单执行门禁的唯一事实来源；风险门禁仍依赖 risk_alert 和账户限制。",
            "table_kind": "event",
            "permission_level": "internal",
            "allowed_roles_json": ["customer_service", "customer_manager", "operations", "risk_manager", "auditor", "super_admin"],
            "aliases_json": ["工单", "客服工单", "人工服务", "补件任务"],
            "is_queryable": True,
            "columns": [
                ("ticket_no", "工单编号", "业务展示的工单号码。", "identifier", "", False, False, True, False, ["工单号", "服务单号"], []),
                ("customer_id", "客户 ID", "工单涉及的客户，关联 users.id。", "identifier", "", True, True, True, False, ["客户", "用户ID"], []),
                ("assigned_to", "处理人 ID", "当前被分派的员工，关联 users.id。", "identifier", "", True, True, True, False, ["处理人员", "负责人"], []),
                ("category", "工单类别", "客户服务、风险调查等业务分类。", "category", "", False, False, True, False, ["工单类型", "服务类型"], []),
                ("status", "工单状态", "待处理、待客户补充、处理中、已解决等状态。", "status", "", False, False, True, False, ["处理进度", "工单进度"], []),
                ("created_at", "创建时间", "工单创建时间。", "datetime", "", False, False, True, False, ["建单时间", "提交时间"], []),
            ],
        },
        {
            "domain_code": "service_operations",
            "table_name": "audit_logs",
            "display_name": "审计日志",
            "description": "关键业务动作、操作人、资源对象和详情的不可替代审计记录。",
            "business_purpose": "用于审计追踪、合规复核和问题定位。",
            "grain_description": "一行代表一次已记录的业务或管理操作。",
            "usage_guidance": "仅用于审计、追溯和合规查询。",
            "non_usage_guidance": "不能代替业务事实表；不能向非审计授权角色开放。",
            "table_kind": "event",
            "permission_level": "restricted",
            "allowed_roles_json": ["auditor", "super_admin"],
            "aliases_json": ["审计", "操作日志", "审计记录"],
            "is_queryable": False,
            "columns": [
                ("actor_user_id", "操作人 ID", "执行操作的用户。", "identifier", "", True, True, False, False, ["操作人", "员工"], []),
                ("action", "操作动作", "被审计的业务动作编码。", "category", "", False, False, False, False, ["行为", "操作类型"], []),
                ("resource_type", "资源类型", "被操作的资源类型。", "category", "", False, False, False, False, ["对象类型"], []),
                ("resource_id", "资源 ID", "被操作资源的标识。", "identifier", "", True, False, False, False, ["对象编号"], []),
                ("created_at", "记录时间", "审计日志写入时间。", "datetime", "", False, False, False, False, ["操作时间", "审计时间"], []),
            ],
        },
    ],
    "relations": [
        ("customer_profile", "user_id", "users", "id", "many_to_one", "客户画像归属于一个用户。", "customer_profile.user_id = users.id"),
        ("customer_asset_snapshot", "user_id", "users", "id", "many_to_one", "资产快照归属于一个客户。", "customer_asset_snapshot.user_id = users.id"),
        ("customer_holding", "user_id", "users", "id", "many_to_one", "持仓归属于一个客户。", "customer_holding.user_id = users.id"),
        ("customer_holding", "account_id", "account", "id", "many_to_one", "持仓属于一个交易账户。", "customer_holding.account_id = account.id"),
        ("customer_holding", "product_id", "product", "id", "many_to_one", "持仓对应一个产品。", "customer_holding.product_id = product.id"),
        ("account", "user_id", "users", "id", "many_to_one", "交易账户归属于一个客户。", "account.user_id = users.id"),
        ("orders", "user_id", "users", "id", "many_to_one", "订单由一个客户发起。", "orders.user_id = users.id"),
        ("orders", "account_id", "account", "id", "many_to_one", "订单使用一个交易账户。", "orders.account_id = account.id"),
        ("orders", "product_id", "product", "id", "many_to_one", "订单对应一个产品。", "orders.product_id = product.id"),
        ("trades", "order_id", "orders", "id", "many_to_one", "成交由一个订单产生。", "trades.order_id = orders.id"),
        ("risk_alert", "user_id", "users", "id", "many_to_one", "风险预警针对一个客户。", "risk_alert.user_id = users.id"),
        ("work_order", "customer_id", "users", "id", "many_to_one", "服务工单关联一个客户。", "work_order.customer_id = users.id"),
        ("work_order", "assigned_to", "users", "id", "many_to_one", "服务工单可分派给一个员工。", "work_order.assigned_to = users.id"),
    ],
    "examples": [
        {
            "domain_code": "customer_profile",
            "question": "查询客户过去三个月的净资产趋势",
            "normalized_question": "查询客户过去三个月的净资产趋势",
            "intent_code": "customer_net_asset_trend",
            "target_table_names_json": ["customer_asset_snapshot", "users"],
            "target_column_names_json": ["customer_asset_snapshot.user_id", "customer_asset_snapshot.snapshot_date", "customer_asset_snapshot.net_asset", "users.username"],
            "sql_template": "SELECT snapshot_date, net_asset FROM customer_asset_snapshot WHERE user_id = :user_id AND snapshot_date >= :start_date ORDER BY snapshot_date",
            "explanation": "按客户和日期读取资产快照；不要使用 customer_profile 代替历史资产数据。",
            "allowed_roles_json": ["advisor", "customer_manager", "risk_manager", "operations", "auditor", "super_admin"],
            "metadata_json": {"parameters": {"user_id": {"type": "subject_id", "source": "authorized_scope_only", "required": True}, "date_range": {"type": "date_range", "source": "natural_language", "start_param": "start_date", "end_param": "end_date", "default_days": 90, "max_range_days": 90}}},
        },
        {
            "domain_code": "trading",
            "question": "统计本月待审核订单金额",
            "normalized_question": "统计本月待审核订单金额",
            "intent_code": "monthly_pending_order_amount",
            "target_table_names_json": ["orders"],
            "target_column_names_json": ["orders.status", "orders.amount", "orders.created_at"],
            "sql_template": "SELECT COALESCE(SUM(amount), 0) AS pending_amount FROM orders WHERE status = 'pending_review' AND created_at >= :month_start",
            "explanation": "订单金额来自 orders.amount，最终成交金额应查询 trades.amount。",
            "allowed_roles_json": ["operations", "risk_manager", "auditor", "super_admin"],
            "metadata_json": {"parameters": {"month_start": {"type": "month_start", "source": "system_now", "required": True}}},
        },
        {
            "domain_code": "risk",
            "question": "查看尚未结案的高风险预警",
            "normalized_question": "查看尚未结案的高风险预警",
            "intent_code": "open_high_risk_alerts",
            "target_table_names_json": ["risk_alert", "users"],
            "target_column_names_json": ["risk_alert.risk_level", "risk_alert.status", "risk_alert.created_at", "users.display_name"],
            "sql_template": "SELECT r.*, u.display_name FROM risk_alert r JOIN users u ON r.user_id = u.id WHERE r.risk_level IN ('high', 'critical') AND r.status NOT IN ('closed', 'resolved') ORDER BY r.created_at DESC LIMIT :limit",
            "explanation": "仅风险/审计授权角色可查询，结果必须按数据范围过滤。",
            "allowed_roles_json": ["risk_manager", "auditor", "super_admin"],
            "metadata_json": {"parameters": {"limit": {"type": "integer", "source": "natural_language", "min": 1, "max": 100, "default": 100}}},
        },
    ],
}


def _column_payload(table_id: str, ordinal: int, item: tuple) -> DataCatalogColumn:
    (
        column_name,
        display_name,
        description,
        semantic_type,
        unit,
        is_primary_key,
        is_foreign_key,
        is_queryable,
        is_sensitive,
        aliases,
        example_values,
    ) = item
    aggregations = ["count"]
    if semantic_type in {"money", "number"}:
        aggregations.extend(["sum", "avg", "min", "max"])
    return DataCatalogColumn(
        id=str(uuid4()),
        table_id=table_id,
        column_name=column_name,
        display_name=display_name,
        description=description,
        business_meaning=description,
        data_type="catalog_seed",
        semantic_type=semantic_type,
        unit=unit,
        ordinal_position=ordinal,
        is_nullable=not is_primary_key,
        is_primary_key=is_primary_key,
        is_foreign_key=is_foreign_key,
        is_sensitive=is_sensitive,
        is_queryable=is_queryable,
        allowed_aggregations_json=aggregations if is_queryable else [],
        aliases_json=aliases,
        example_values_json=example_values,
    )


async def ensure_data_catalog_seed(database: Database) -> None:
    async with database.session_factory() as session:
        domain_map: dict[str, DataCatalogDomain] = {}
        for payload in CORE_CATALOG["domains"]:
            domain = (await session.execute(select(DataCatalogDomain).where(DataCatalogDomain.code == payload["code"]))).scalar_one_or_none()
            if domain is None:
                domain = DataCatalogDomain(id=str(uuid4()), **payload)
                session.add(domain)
            else:
                for key, value in payload.items():
                    setattr(domain, key, value)
            domain_map[payload["code"]] = domain
        await session.flush()

        table_map: dict[str, DataCatalogTable] = {}
        for payload in CORE_CATALOG["tables"]:
            table = (await session.execute(select(DataCatalogTable).where(DataCatalogTable.table_name == payload["table_name"], DataCatalogTable.schema_name == "public", DataCatalogTable.database_name == "wealth_manager"))).scalar_one_or_none()
            values = {key: value for key, value in payload.items() if key not in {"domain_code", "columns"}}
            values["domain_id"] = domain_map[payload["domain_code"]].id
            if table is None:
                table = DataCatalogTable(id=str(uuid4()), **values)
                session.add(table)
                await session.flush()
            else:
                for key, value in values.items():
                    setattr(table, key, value)
            table_map[payload["table_name"]] = table
            existing_columns = {
                column.column_name: column
                for column in (await session.execute(select(DataCatalogColumn).where(DataCatalogColumn.table_id == table.id))).scalars().all()
            }
            for ordinal, item in enumerate(payload["columns"], start=1):
                column = existing_columns.get(item[0])
                seeded = _column_payload(table.id, ordinal, item)
                if column is None:
                    session.add(seeded)
                else:
                    for key in (
                        "display_name", "description", "business_meaning", "semantic_type", "unit", "ordinal_position",
                        "is_primary_key", "is_foreign_key", "is_queryable", "is_sensitive", "allowed_aggregations_json",
                        "aliases_json", "example_values_json",
                    ):
                        setattr(column, key, getattr(seeded, key))
        await session.flush()

        existing_relations = {
            (item.from_table_id, item.from_column_name, item.to_table_id, item.to_column_name): item
            for item in (await session.execute(select(DataCatalogRelation))).scalars().all()
        }
        for from_table, from_column, to_table, to_column, cardinality, description, join_guidance in CORE_CATALOG["relations"]:
            key = (table_map[from_table].id, from_column, table_map[to_table].id, to_column)
            relation = existing_relations.get(key)
            values = {
                "from_table_id": key[0],
                "from_column_name": from_column,
                "to_table_id": key[2],
                "to_column_name": to_column,
                "relation_type": "foreign_key",
                "cardinality": cardinality,
                "description": description,
                "join_guidance": join_guidance,
                "is_preferred": True,
                "is_active": True,
            }
            if relation is None:
                session.add(DataCatalogRelation(id=str(uuid4()), **values))
            else:
                for field, value in values.items():
                    setattr(relation, field, value)

        for payload in CORE_CATALOG["examples"]:
            query = (await session.execute(select(DataCatalogExampleQuery).where(DataCatalogExampleQuery.intent_code == payload["intent_code"]))).scalar_one_or_none()
            values = {**payload, "domain_id": domain_map[payload.pop("domain_code")].id} if False else None
            values = {key: value for key, value in payload.items() if key != "domain_code"}
            values["domain_id"] = domain_map[payload["domain_code"]].id
            values["is_approved"] = True
            values["is_active"] = True
            if query is None:
                session.add(DataCatalogExampleQuery(id=str(uuid4()), **values))
            else:
                for key, value in values.items():
                    setattr(query, key, value)
        await session.commit()
