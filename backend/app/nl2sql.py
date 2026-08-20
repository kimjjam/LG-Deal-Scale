import re

from sqlglot import exp, parse
from sqlglot.errors import ParseError

SCHEMA_WHITELIST: dict[str, set[str]] = {
    "accounts": {"id", "name", "phone", "attributes", "created_at"},
    "contacts": {"id", "account_id", "name", "role", "phone", "email"},
    "inquiries": {"id", "account_id", "channel", "content", "status", "created_at"},
    "interactions": {"id", "account_id", "type", "amount", "created_at"},
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
    "staff": {"id", "name", "email", "role"},
    "leads": {
        "id",
        "name",
        "address",
        "license_date",
        "years_in_business",
        "business_type",
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
    "products": {"id", "name", "brand", "category", "price", "product_url", "updated_at"},
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
    tables = {table.name for table in tree.find_all(exp.Table)}
    if not tables or not tables.issubset(SCHEMA_WHITELIST):
        raise UnsafeQueryError("허용되지 않은 테이블입니다.")
    table_aliases = {table.alias_or_name: table.name for table in tree.find_all(exp.Table)}
    allowed_unqualified = set().union(*(SCHEMA_WHITELIST[table] for table in tables))
    for column in tree.find_all(exp.Column):
        if column.name == "*":
            continue
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

