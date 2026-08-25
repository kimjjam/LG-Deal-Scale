import sqlite3
from pathlib import Path

import pytest
from alembic.config import Config

from alembic import command
from app.config import get_settings


def test_sqlite_phase4_migration_round_trip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "phase4.db"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            PRAGMA foreign_keys=ON;
            CREATE TABLE accounts (id INTEGER PRIMARY KEY);
            CREATE TABLE staff (id CHAR(32) PRIMARY KEY);
            CREATE TABLE leads (
                id INTEGER PRIMARY KEY,
                pipeline_stage VARCHAR(30) NOT NULL,
                created_at DATETIME NOT NULL
            );
            CREATE TABLE opportunities (id INTEGER PRIMARY KEY);
            CREATE TABLE products (
                id INTEGER PRIMARY KEY, name VARCHAR(200) NOT NULL,
                brand VARCHAR(100) NOT NULL, category VARCHAR(100) NOT NULL,
                price NUMERIC(14, 2) NOT NULL, product_url VARCHAR(1000) NOT NULL,
                updated_at DATETIME NOT NULL
            );
            CREATE TABLE inquiries (
                id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL REFERENCES accounts(id),
                channel VARCHAR(30) NOT NULL, content TEXT NOT NULL, raw_conversation JSON,
                status VARCHAR(20) NOT NULL,
                created_at DATETIME NOT NULL,
                CONSTRAINT ck_inquiry_status CHECK (status IN ('open', 'routed', 'resolved'))
            );
            CREATE TABLE assignments (
                id INTEGER PRIMARY KEY, inquiry_id INTEGER NOT NULL REFERENCES inquiries(id),
                assignee_id CHAR(32) NOT NULL REFERENCES staff(id), assigned_at DATETIME NOT NULL,
                method VARCHAR(20) NOT NULL,
                CONSTRAINT ck_assignment_method CHECK (method IN ('round_robin', 'manual'))
            );
            CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY);
            INSERT INTO leads VALUES (1, 'follow_up_due', '2026-08-01 09:00:00');
            INSERT INTO alembic_version VALUES ('0003');
            """
        )
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{database.as_posix()}")
    get_settings.cache_clear()
    config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
    try:
        with pytest.raises(RuntimeError, match="require PostgreSQL"):
            command.upgrade(config, "head")
        config.set_main_option("allow_sqlite_tests", "true")
        command.upgrade(config, "head")
        with sqlite3.connect(database) as connection:
            assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
                "0010",
            )
            lead_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(leads)").fetchall()
            }
            assert {
                "assignee_id",
                "contact_name",
                "contact_phone",
                "contact_email",
                "next_action_at",
            }.issubset(lead_columns)
            assert connection.execute(
                "SELECT pipeline_stage, next_action_at FROM leads WHERE id = 1"
            ).fetchone() == ("follow_up_due", "2026-08-01 09:00:00")
            assert {
                "opportunity_id",
                "product_id",
                "product_name",
                "quantity",
                "unit_price",
            }.issubset(
                {row[1] for row in connection.execute("PRAGMA table_info(opportunity_items)")}
            )
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO products "
                    "(id, name, brand, category, price, price_type, is_verified, product_url, updated_at) "
                    "VALUES (1, '잘못된 가격', '가상', '기타', 0, 'retail_reference', 0, "
                    "'https://example.test/product', CURRENT_TIMESTAMP)"
                )
            connection.execute("INSERT INTO staff VALUES ('manager-a')")
            connection.execute("INSERT INTO staff VALUES ('manager-b')")
            connection.execute(
                "INSERT INTO sales_regions "
                "(region_name, match_keyword, manager_id, is_active, created_at) VALUES "
                "('서울', '서울', 'manager-a', 1, CURRENT_TIMESTAMP), "
                "('서울', '서울', 'manager-b', 1, CURRENT_TIMESTAMP)"
            )
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO sales_regions "
                    "(region_name, match_keyword, manager_id, is_active, created_at) VALUES "
                    "('서울', '서울', 'manager-a', 1, CURRENT_TIMESTAMP)"
                )
            connection.execute("INSERT INTO accounts VALUES (1)")
            connection.execute("INSERT INTO staff VALUES ('rep')")
            connection.execute(
                "INSERT INTO inquiries "
                "(id, account_id, channel, content, status, created_at) "
                "VALUES (1, 1, 'web', '문의', 'routed', CURRENT_TIMESTAMP)"
            )
            connection.execute(
                "INSERT INTO assignments "
                "(id, inquiry_id, assignee_id, assigned_at, method) "
                "VALUES (1, 1, 'rep', CURRENT_TIMESTAMP, 'claimed')"
            )
            connection.commit()

        command.downgrade(config, "0004")
        with sqlite3.connect(database) as connection:
            assert connection.execute("SELECT method FROM assignments").fetchone() == ("manual",)
        command.upgrade(config, "head")
    finally:
        get_settings.cache_clear()
