"""Migration / schema-level validation."""
from __future__ import annotations

import enum

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import ENUM as PGEnum

from app.models import Base
from tests.conftest import alembic_config, create_database, drop_database

EXPECTED_TABLES = 31
OUR_FUNCTIONS = {"set_updated_at", "enforce_immutable", "enforce_minor_units", "check_run_projections",
                 "scenario_run_before_delete", "scenario_run_after_delete", "check_comparison_item_count"}


def test_all_models_have_a_table(session):
    names = {r[0] for r in session.execute(text(
        "select table_name from information_schema.tables where table_schema='public' "
        "and table_type='BASE TABLE' and table_name<>'alembic_version'"))}
    assert names == set(Base.metadata.tables) and len(names) == EXPECTED_TABLES


def test_models_and_migration_are_in_sync(engine):
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": True, "compare_server_default": True})
        assert compare_metadata(ctx, Base.metadata) == []


def test_extensions_enums_functions_view_exist(session):
    ext = {r[0] for r in session.execute(text("select extname from pg_extension"))}
    assert {"citext", "pg_trgm", "btree_gin"} <= ext
    funcs = {r[0] for r in session.execute(text(
        "select proname from pg_proc p join pg_namespace n on n.oid=p.pronamespace where n.nspname='public'"))}
    assert OUR_FUNCTIONS <= funcs
    assert session.scalar(text("select count(*) from pg_views where viewname='v_goal_current_amounts'")) == 1


def test_python_enums_match_database_enums(session):
    db = {r[0]: r[1].split(",") for r in session.execute(text(
        "select t.typname, string_agg(e.enumlabel, ',' order by e.enumsortorder) from pg_type t "
        "join pg_enum e on e.enumtypid=t.oid group by 1"))}
    seen = {}
    for table in Base.metadata.tables.values():
        for col in table.columns:
            if isinstance(col.type, PGEnum):
                seen[col.type.name] = [m.value for m in col.type.enum_class]
    for name, py_values in seen.items():
        assert db[name] == py_values, name
    assert set(seen) == set(db)  # no unused / undeclared enum types


def test_money_arrays_use_exact_numeric(session):
    udt = session.scalar(text("select udt_name from information_schema.columns where table_name="
                              "'investment_simulations' and column_name='alternate_returns'"))
    assert udt == "_numeric"


def test_no_floating_point_or_money_types(session):
    bad = session.execute(text(
        "select table_name, column_name, data_type from information_schema.columns where table_schema='public' "
        "and data_type in ('real','double precision','money')")).all()
    assert bad == []


def test_money_columns_are_numeric_18_4(session):
    rows = session.execute(text(
        "select c.table_name, c.column_name, c.numeric_precision, c.numeric_scale from information_schema.columns c "
        "join information_schema.tables t using (table_schema, table_name) "
        "where c.table_schema='public' and t.table_type='BASE TABLE' and c.data_type='numeric'")).all()
    allowed = {(18, 4), (9, 6), (5, 4)}  # money, rates/ratios, thresholds
    assert rows and all((p, sc) in allowed for _, _, p, sc in rows)
    money = [r for r in rows if (r[2], r[3]) == (18, 4)]
    assert len(money) == 38  # every amount column in the 31 base tables


def test_all_timestamps_are_timezone_aware(session):
    bad = session.execute(text(
        "select table_name, column_name from information_schema.columns where table_schema='public' "
        "and data_type = 'timestamp without time zone'")).all()
    assert bad == []


def test_every_user_owned_table_has_an_ownership_path(session):
    """Every table either has user_id (FK -> users) or is a child whose FK carries user_id."""
    rows = session.execute(text("""
        select c.table_name,
               exists (select 1 from information_schema.columns u where u.table_name=c.table_name
                       and u.table_schema='public' and u.column_name='user_id') as has_user
        from information_schema.tables c where c.table_schema='public' and c.table_type='BASE TABLE'
          and c.table_name not in ('alembic_version','currencies','users')""")).all()
    assert [t for t, has in rows if not has] == []
    # and every user_id column is covered by a foreign key that includes it (directly or composite)
    uncovered = session.execute(text("""
        select c.relname from pg_class c join pg_namespace n on n.oid=c.relnamespace
        join pg_attribute a on a.attrelid=c.oid and a.attname='user_id' and not a.attisdropped
        where n.nspname='public' and c.relkind='r' and c.relname not in ('audit_log')
          and not exists (select 1 from pg_constraint k where k.conrelid=c.oid and k.contype='f' and a.attnum = any(k.conkey))
          and c.relname<>'users'""")).all()
    assert uncovered == []


def test_migration_round_trip_downgrade_and_upgrade():
    name, url = create_database()
    try:
        cfg = alembic_config(url)
        command.upgrade(cfg, "head")
        eng = create_engine(url)
        count = lambda q: eng.connect().execute(text(q)).scalar()
        base_tables = ("select count(*) from information_schema.tables where table_schema='public' "
                       "and table_type='BASE TABLE' and table_name<>'alembic_version'")
        assert count(base_tables) == EXPECTED_TABLES
        command.downgrade(cfg, "base")
        assert count(base_tables) == 0
        assert count("select count(*) from pg_views where schemaname='public'") == 0
        assert count("select count(*) from pg_type t join pg_namespace n on n.oid=t.typnamespace "
                     "where n.nspname='public' and t.typtype='e'") == 0
        left = {r[0] for r in eng.connect().execute(text(
            "select proname from pg_proc p join pg_namespace n on n.oid=p.pronamespace "
            "where n.nspname='public' and not exists (select 1 from pg_depend d where d.objid=p.oid and d.deptype='e')"))}
        assert not (OUR_FUNCTIONS & left)
        command.upgrade(cfg, "head")  # re-applies cleanly after a full rollback
        assert count("select count(*) from currencies") == 4
        eng.dispose()
    finally:
        drop_database(name)
