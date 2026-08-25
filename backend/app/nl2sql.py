import re

from sqlglot import exp, parse
from sqlglot.errors import ParseError

SCHEMA_WHITELIST: dict[str, set[str]] = {
    "accounts": {"id", "name", "phone", "attributes", "created_at", "deleted_at"},
    "contacts": {"id", "account_id", "name", "role", "phone", "email", "deleted_at"},
    "inquiries": {
        "id",
        "account_id",
        "channel",
        "content",
        "routing_manager_id",
        "partner_id",
        "status",
        "created_at",
    },
    "interactions": {
        "id",
        "account_id",
        "staff_id",
        "contact_id",
        "inquiry_id",
        "opportunity_id",
        "type",
        "content",
        "outcome",
        "amount",
        "created_at",
    },
    "scores": {
        "id",
        "inquiry_id",
        "fit_score",
        "intent_score",
        "intent_category",
        "intent_confidence",
        "recency_score",
        "total_score",
        "scoring_version",
        "created_at",
    },
    "assignments": {"id", "inquiry_id", "assignee_id", "assigned_at", "method"},
    "staff": {"id", "name", "email", "role", "is_active"},
    "leads": {
        "id",
        "name",
        "address",
        "license_date",
        "years_in_business",
        "business_type",
        "assignee_id",
        "contact_name",
        "contact_phone",
        "contact_email",
        "next_action_at",
        "lead_score",
        "pipeline_stage",
        "created_at",
    },
    "outbound_drafts": {
        "id",
        "lead_id",
        "sequence_step",
        "subject",
        "generated_at",
        "reviewed_by",
        "send_mode",
        "sent_at",
    },
    "products": {
        "id",
        "name",
        "brand",
        "category",
        "price",
        "price_type",
        "price_source_url",
        "price_verified_at",
        "usage_context",
        "is_verified",
        "product_url",
        "updated_at",
    },
    "sales_regions": {
        "id",
        "region_name",
        "match_keyword",
        "manager_id",
        "is_active",
        "created_at",
    },
    "partners": {
        "id",
        "name",
        "address",
        "phone",
        "region",
        "partner_type",
        "verification_source",
        "verified_at",
        "is_active",
        "created_at",
    },
    "opportunities": {
        "id",
        "account_id",
        "inquiry_id",
        "lead_id",
        "assignee_id",
        "title",
        "amount",
        "probability",
        "expected_close_date",
        "stage",
        "loss_reason",
        "created_at",
        "updated_at",
    },
    "tasks": {
        "id",
        "account_id",
        "opportunity_id",
        "inquiry_id",
        "assignee_id",
        "title",
        "due_at",
        "status",
        "completed_at",
        "created_at",
    },
    "opportunity_stage_history": {
        "id",
        "opportunity_id",
        "stage",
        "changed_by",
        "changed_at",
    },
    "opportunity_items": {
        "id",
        "opportunity_id",
        "product_id",
        "product_name",
        "quantity",
        "unit_price",
    },
}
ALLOWED_FUNCTIONS = {
    "AVG",
    "CAST",
    "COALESCE",
    "COUNT",
    "CURRENT_DATE",
    "DATE_TRUNC",
    "EXTRACT",
    "LOWER",
    "MAX",
    "MIN",
    "SUM",
    "TIMESTAMP_TRUNC",
    "UPPER",
}


class UnsafeQueryError(ValueError):
    pass


def _strip_comments(sql: str) -> str:
    without_blocks = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return re.sub(r"--[^\r\n]*", " ", without_blocks).strip()


def validate_sql(sql: str) -> str:
    cleaned = _strip_comments(sql)
    try:
        statements = parse(cleaned, read="postgres")
    except ParseError as error:
        raise UnsafeQueryError("SQL을 해석할 수 없습니다.") from error
    if len(statements) != 1:
        raise UnsafeQueryError("하나의 SQL 문만 허용됩니다.")
    tree = statements[0]
    if not isinstance(tree, exp.Select) or tree.find(exp.CTE):
        raise UnsafeQueryError("CTE 없는 SELECT 문만 허용됩니다.")
    forbidden = (exp.Delete, exp.Update, exp.Insert, exp.Drop, exp.Alter, exp.Create, exp.Command)
    if any(tree.find(kind) for kind in forbidden):
        raise UnsafeQueryError("데이터를 변경하는 SQL은 허용되지 않습니다.")
    if any(
        not isinstance(star.parent, exp.Count) or star.arg_key != "this"
        for star in tree.find_all(exp.Star)
    ):
        raise UnsafeQueryError("조회 항목의 와일드카드는 허용되지 않습니다.")
    for function in tree.find_all(exp.Func):
        name = function.name if isinstance(function, exp.Anonymous) else function.sql_name()
        if name.upper() not in ALLOWED_FUNCTIONS:
            raise UnsafeQueryError(f"허용되지 않은 함수입니다: {name}")
    table_nodes = list(tree.find_all(exp.Table))
    if any(table.catalog or (table.db and table.db != "public") for table in table_nodes):
        raise UnsafeQueryError("public 스키마의 테이블만 허용됩니다.")
    tables = {table.name for table in table_nodes}
    if not tables or not tables.issubset(SCHEMA_WHITELIST):
        raise UnsafeQueryError("허용되지 않은 테이블입니다.")
    table_aliases = {table.alias_or_name: table.name for table in tree.find_all(exp.Table)}
    allowed_unqualified = set().union(*(SCHEMA_WHITELIST[table] for table in tables))
    for column in tree.find_all(exp.Column):
        table_name = table_aliases.get(column.table, column.table) if column.table else None
        allowed = SCHEMA_WHITELIST.get(table_name, allowed_unqualified)
        if column.name not in allowed:
            raise UnsafeQueryError(f"허용되지 않은 컬럼입니다: {column.name}")
    limit = tree.args.get("limit")
    if not limit:
        tree = tree.limit(200)
    else:
        expression = limit.expression
        if not isinstance(expression, exp.Literal) or not expression.is_int:
            raise UnsafeQueryError("LIMIT은 정수여야 합니다.")
        if int(expression.this) > 200:
            tree.set("limit", exp.Limit(expression=exp.Literal.number(200)))
    return tree.sql(dialect="postgres")
