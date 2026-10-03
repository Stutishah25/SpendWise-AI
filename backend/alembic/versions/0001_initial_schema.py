"""initial schema: tables, enums, trigger functions, triggers, view, currency seed

Revision ID: 0001
Revises: 
Create Date: 2026-10-02 18:48:30.641106
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None

# ---- frozen SQL objects that autogenerate cannot express (copied from the reviewed v1.1 schema) ----
EXTENSIONS = ['citext', 'pg_trgm', 'btree_gin']
ENUMS = [
    ('txn_kind', "CREATE TYPE txn_kind AS ENUM ('income','expense')"),
    ('txn_source', "CREATE TYPE txn_source AS ENUM ('manual','recurring','import')"),
    ('recurrence_frequency', "CREATE TYPE recurrence_frequency AS ENUM ('daily','weekly','monthly','quarterly','yearly')"),
    ('goal_type', "CREATE TYPE goal_type AS ENUM ('emergency_fund','laptop','education','travel','car','other')"),
    ('goal_status', "CREATE TYPE goal_status AS ENUM ('active','completed','paused','cancelled')"),
    ('goal_entry_kind', "CREATE TYPE goal_entry_kind AS ENUM ('contribution','withdrawal')"),
    ('loan_type', "CREATE TYPE loan_type AS ENUM ('education','personal','vehicle','home','credit_card','other')"),
    ('loan_status', "CREATE TYPE loan_status AS ENUM ('active','paid_off','cancelled')"),
    ('loan_payment_type', "CREATE TYPE loan_payment_type AS ENUM ('emi','prepayment','fee')"),
    ('contribution_frequency', "CREATE TYPE contribution_frequency AS ENUM ('monthly','quarterly','half_yearly','yearly')"),
    ('period_timing', "CREATE TYPE period_timing AS ENUM ('start_of_period','end_of_period')"),
    ('investment_type', "CREATE TYPE investment_type AS ENUM ('sip','lump_sum','sip_plus_lump_sum')"),
    ('scenario_type', "CREATE TYPE scenario_type AS ENUM ('purchase','loan','investment_plan','education_plan', 'trip_plan','savings_change','income_change','expense_change','custom')"),
    ('scenario_status', "CREATE TYPE scenario_status AS ENUM ('draft','active','archived')"),
    ('calc_type', "CREATE TYPE calc_type AS ENUM ('emi','loan_amortization','present_value','future_value', 'compound_interest','sip','lump_sum','inflation_adjusted', 'savings_projection','opportunity_cost','goal_contribution', 'twin_projection','scenario_simulation','scenario_comparison')"),
    ('snapshot_kind', "CREATE TYPE snapshot_kind AS ENUM ('manual','scheduled','scenario_baseline')"),
    ('projection_kind', "CREATE TYPE projection_kind AS ENUM ('baseline','scenario')"),
    ('ai_task', "CREATE TYPE ai_task AS ENUM ('explain_scenario','compare_scenarios','explain_investment', 'explain_concept','spending_patterns','monthly_summary','goal_insights')"),
    ('ai_validation_status', "CREATE TYPE ai_validation_status AS ENUM ('passed','failed_fallback_used','skipped')"),
    ('notification_type', "CREATE TYPE notification_type AS ENUM ('goal_reminder','budget_alert','emi_reminder','monthly_summary','system')"),
    ('notification_severity', "CREATE TYPE notification_severity AS ENUM ('info','warning','critical')"),
    ('delivery_channel', "CREATE TYPE delivery_channel AS ENUM ('in_app','email')"),
    ('delivery_status', "CREATE TYPE delivery_status AS ENUM ('pending','sent','failed','skipped')"),
    ('auth_token_purpose', "CREATE TYPE auth_token_purpose AS ENUM ('email_verification','password_reset')"),
    ('audit_outcome', "CREATE TYPE audit_outcome AS ENUM ('success','denied','failure')"),
]
FUNCTIONS = [
    ('set_updated_at', """CREATE FUNCTION set_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at := now(); RETURN NEW; END $$;"""),
    ('enforce_immutable', """CREATE FUNCTION enforce_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE allowed text[] := COALESCE(TG_ARGV, ARRAY[]::text[]);   -- TG_ARGV is NULL (not '{}') when no args are given
BEGIN
  IF (to_jsonb(NEW) - allowed) IS DISTINCT FROM (to_jsonb(OLD) - allowed) THEN
    RAISE EXCEPTION 'table % is immutable (mutable columns: %)', TG_TABLE_NAME,
      COALESCE(NULLIF(array_to_string(allowed, ', '), ''), 'none')
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  RETURN NEW;
END $$;"""),
    ('enforce_minor_units', """CREATE FUNCTION enforce_minor_units() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE col text; v numeric; ms smallint;
BEGIN
  SELECT minor_units INTO ms FROM currencies WHERE code = NEW.currency;
  FOREACH col IN ARRAY TG_ARGV LOOP
    v := (to_jsonb(NEW) ->> col)::numeric;
    IF v IS NOT NULL AND ms IS NOT NULL AND scale(trim_scale(v)) > ms THEN
      RAISE EXCEPTION '%.% has more than % decimal places for currency %', TG_TABLE_NAME, col, ms, NEW.currency
        USING ERRCODE = 'check_violation';
    END IF;
  END LOOP;
  RETURN NEW;
END $$;"""),
    ('check_run_projections', """CREATE FUNCTION check_run_projections() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE bk projection_kind; sk projection_kind; bh integer; sh integer;
BEGIN
  SELECT kind, horizon_months INTO bk, bh FROM financial_projections WHERE id = NEW.baseline_projection_id;
  SELECT kind, horizon_months INTO sk, sh FROM financial_projections WHERE id = NEW.scenario_projection_id;
  IF bk IS DISTINCT FROM 'baseline' OR sk IS DISTINCT FROM 'scenario' THEN
    RAISE EXCEPTION 'scenario_runs requires a baseline projection and a scenario projection'
      USING ERRCODE = 'check_violation';
  END IF;
  IF bh <> NEW.horizon_months OR sh <> NEW.horizon_months THEN
    RAISE EXCEPTION 'projection horizon must equal the run horizon' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;"""),
    ('scenario_run_before_delete', """CREATE FUNCTION scenario_run_before_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  DELETE FROM scenario_comparisons c USING scenario_comparison_items i
   WHERE i.scenario_run_id = OLD.id AND i.user_id = OLD.user_id AND c.id = i.comparison_id AND c.user_id = i.user_id;
  RETURN OLD;
END $$;"""),
    ('scenario_run_after_delete', """CREATE FUNCTION scenario_run_after_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  DELETE FROM financial_projections p
   WHERE p.user_id = OLD.user_id
     AND p.id IN (OLD.baseline_projection_id, OLD.scenario_projection_id)
     AND NOT EXISTS (SELECT 1 FROM scenario_runs r
                      WHERE r.baseline_projection_id = p.id OR r.scenario_projection_id = p.id);
  RETURN NULL;
END $$;"""),
    ('check_comparison_item_count', """CREATE FUNCTION check_comparison_item_count() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE cid uuid; n integer;
BEGIN
  IF TG_OP = 'INSERT' THEN cid := NEW.id; ELSE cid := OLD.comparison_id; END IF;
  IF EXISTS (SELECT 1 FROM scenario_comparisons WHERE id = cid) THEN
    SELECT count(*) INTO n FROM scenario_comparison_items WHERE comparison_id = cid;
    IF n NOT BETWEEN 2 AND 3 THEN
      RAISE EXCEPTION 'comparison % must contain 2 to 3 items (has %)', cid, n USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  RETURN NULL;
END $$;"""),
]
UPDATED_AT_TRIGGERS = """DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['users','user_profiles','user_preferences','categories','recurring_rules',
    'transactions','budgets','goals','loans','loan_payments','investment_simulations','scenarios',
    'scenario_comparisons','notifications','notification_deliveries','notification_preferences'] LOOP
    EXECUTE format('CREATE TRIGGER trg_%s_updated_at BEFORE UPDATE ON %I
                    FOR EACH ROW EXECUTE FUNCTION set_updated_at()', t, t);
  END LOOP;
END $$;"""
VIEWS = [
    ('v_goal_current_amounts', """CREATE VIEW v_goal_current_amounts WITH (security_invoker = true) AS
SELECT g.id AS goal_id, g.user_id, g.currency, g.target_amount,
       g.starting_amount + COALESCE(SUM(CASE c.kind WHEN 'contribution' THEN c.amount ELSE -c.amount END), 0)
         AS current_amount
FROM goals g
LEFT JOIN goal_contributions c ON c.goal_id = g.id AND c.user_id = g.user_id
GROUP BY g.id"""),
]
TRIGGERS = [
    'CREATE TRIGGER trg_scenario_runs_check BEFORE INSERT ON scenario_runs FOR EACH ROW EXECUTE FUNCTION check_run_projections()',
    'CREATE TRIGGER trg_scenario_runs_before_delete BEFORE DELETE ON scenario_runs FOR EACH ROW EXECUTE FUNCTION scenario_run_before_delete()',
    'CREATE TRIGGER trg_scenario_runs_after_delete AFTER DELETE ON scenario_runs FOR EACH ROW EXECUTE FUNCTION scenario_run_after_delete()',
    'CREATE TRIGGER trg_immutable BEFORE UPDATE ON financial_snapshots FOR EACH ROW EXECUTE FUNCTION enforce_immutable()',
    'CREATE TRIGGER trg_immutable BEFORE UPDATE ON financial_projections FOR EACH ROW EXECUTE FUNCTION enforce_immutable()',
    'CREATE TRIGGER trg_immutable BEFORE UPDATE ON financial_projection_points FOR EACH ROW EXECUTE FUNCTION enforce_immutable()',
    'CREATE TRIGGER trg_immutable BEFORE UPDATE ON scenario_runs FOR EACH ROW EXECUTE FUNCTION enforce_immutable()',
    'CREATE TRIGGER trg_immutable BEFORE UPDATE ON scenario_comparison_items FOR EACH ROW EXECUTE FUNCTION enforce_immutable()',
    'CREATE TRIGGER trg_immutable BEFORE UPDATE ON investment_simulation_runs FOR EACH ROW EXECUTE FUNCTION enforce_immutable()',
    'CREATE TRIGGER trg_immutable BEFORE UPDATE ON goal_progress_snapshots FOR EACH ROW EXECUTE FUNCTION enforce_immutable()',
    'CREATE TRIGGER trg_immutable BEFORE UPDATE ON loan_schedule_items FOR EACH ROW EXECUTE FUNCTION enforce_immutable()',
    'CREATE TRIGGER trg_immutable BEFORE UPDATE ON ai_analyses FOR EACH ROW EXECUTE FUNCTION enforce_immutable()',
    "CREATE TRIGGER trg_immutable BEFORE UPDATE ON loan_schedules FOR EACH ROW EXECUTE FUNCTION enforce_immutable('is_current')",
    "CREATE TRIGGER trg_immutable BEFORE UPDATE ON scenario_comparisons FOR EACH ROW EXECUTE FUNCTION enforce_immutable('name','updated_at')",
    'CREATE CONSTRAINT TRIGGER trg_comparison_items_ins AFTER INSERT ON scenario_comparisons DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_comparison_item_count()',
    'CREATE CONSTRAINT TRIGGER trg_comparison_items_del AFTER DELETE ON scenario_comparison_items DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_comparison_item_count()',
    "CREATE TRIGGER trg_minor_units BEFORE INSERT OR UPDATE ON transactions FOR EACH ROW EXECUTE FUNCTION enforce_minor_units('amount')",
    "CREATE TRIGGER trg_minor_units BEFORE INSERT OR UPDATE ON recurring_rules FOR EACH ROW EXECUTE FUNCTION enforce_minor_units('amount')",
    "CREATE TRIGGER trg_minor_units BEFORE INSERT OR UPDATE ON budgets FOR EACH ROW EXECUTE FUNCTION enforce_minor_units('limit_amount')",
    "CREATE TRIGGER trg_minor_units BEFORE INSERT OR UPDATE ON goals FOR EACH ROW EXECUTE FUNCTION enforce_minor_units('target_amount','starting_amount','planned_monthly_contribution')",
    "CREATE TRIGGER trg_minor_units BEFORE INSERT OR UPDATE ON goal_contributions FOR EACH ROW EXECUTE FUNCTION enforce_minor_units('amount')",
    "CREATE TRIGGER trg_minor_units BEFORE INSERT OR UPDATE ON loans FOR EACH ROW EXECUTE FUNCTION enforce_minor_units('principal')",
    "CREATE TRIGGER trg_minor_units BEFORE INSERT OR UPDATE ON loan_payments FOR EACH ROW EXECUTE FUNCTION enforce_minor_units('amount')",
    "CREATE TRIGGER trg_minor_units BEFORE INSERT OR UPDATE ON investment_simulations FOR EACH ROW EXECUTE FUNCTION enforce_minor_units('initial_amount','periodic_contribution')",
]
CURRENCY_SEED = """INSERT INTO currencies (code, name, symbol, minor_units, default_locale, is_enabled) VALUES
  ('INR','Indian Rupee','₹',2,'en-IN',true),
  ('USD','US Dollar','$',2,'en-US',false),
  ('EUR','Euro','€',2,'de-DE',false),
  ('GBP','Pound Sterling','£',2,'en-GB',false)"""


def upgrade() -> None:
    for ext in EXTENSIONS:  # extensions are left in place on downgrade (they may pre-exist / be shared)
        op.execute(f"CREATE EXTENSION IF NOT EXISTS {ext}")
    for _name, ddl in ENUMS:
        op.execute(ddl)
    for _name, ddl in FUNCTIONS:
        op.execute(ddl)
    op.create_table('currencies',
    sa.Column('code', sa.CHAR(length=3), nullable=False),
    sa.Column('name', sa.String(length=60), nullable=False),
    sa.Column('symbol', sa.String(length=8), nullable=False),
    sa.Column('minor_units', sa.SmallInteger(), nullable=False),
    sa.Column('default_locale', sa.String(length=16), nullable=False),
    sa.Column('is_enabled', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.CheckConstraint("code ~ '^[A-Z]{3}$'", name=op.f('ck_currencies_code_format')),
    sa.CheckConstraint('minor_units BETWEEN 0 AND 4', name=op.f('ck_currencies_minor_units_range')),
    sa.PrimaryKeyConstraint('code', name=op.f('pk_currencies'))
    )
    op.create_table('users',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('email', postgresql.CITEXT(), nullable=False),
    sa.Column('password_hash', sa.Text(), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('email_verified_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('failed_login_count', sa.SmallInteger(), server_default=sa.text('0'), nullable=False),
    sa.Column('locked_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('password_changed_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('char_length(email) BETWEEN 3 AND 254', name=op.f('ck_users_email_length')),
    sa.CheckConstraint('failed_login_count >= 0', name=op.f('ck_users_failed_login_count_non_negative')),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_users')),
    sa.UniqueConstraint('email', name='uq_users_email')
    )
    op.create_table('audit_log',
    sa.Column('id', sa.BigInteger(), sa.Identity(always=True), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=True),
    sa.Column('action', sa.String(length=80), nullable=False),
    sa.Column('entity_type', sa.String(length=50), nullable=True),
    sa.Column('entity_id', sa.UUID(), nullable=True),
    sa.Column('outcome', postgresql.ENUM('success', 'denied', 'failure', name='audit_outcome', create_type=False), nullable=False),
    sa.Column('request_id', sa.String(length=64), nullable=True),
    sa.Column('ip_address', postgresql.INET(), nullable=True),
    sa.Column('metadata', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("jsonb_typeof(metadata) = 'object'", name=op.f('ck_audit_log_metadata_is_object')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_audit_log_user_id_users'), ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_audit_log'))
    )
    op.create_index('ix_audit_action', 'audit_log', ['action', sa.literal_column('created_at DESC')], unique=False)
    op.create_index('ix_audit_entity', 'audit_log', ['entity_type', 'entity_id'], unique=False)
    op.create_index('ix_audit_user_created', 'audit_log', ['user_id', sa.literal_column('created_at DESC')], unique=False)
    op.create_table('auth_tokens',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('purpose', postgresql.ENUM('email_verification', 'password_reset', name='auth_token_purpose', create_type=False), nullable=False),
    sa.Column('token_hash', sa.CHAR(length=64), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name=op.f('ck_auth_tokens_token_hash_hex64')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_auth_tokens_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_auth_tokens')),
    sa.UniqueConstraint('token_hash', name='uq_auth_tokens_token_hash')
    )
    op.create_index('ix_auth_tokens_user_purpose', 'auth_tokens', ['user_id', 'purpose'], unique=False)
    op.create_table('categories',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('kind', postgresql.ENUM('income', 'expense', name='txn_kind', create_type=False), nullable=False),
    sa.Column('name', sa.String(length=60), nullable=False),
    sa.Column('system_key', sa.String(length=40), nullable=True),
    sa.Column('color', sa.CHAR(length=7), nullable=True),
    sa.Column('icon', sa.String(length=40), nullable=True),
    sa.Column('is_archived', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("color ~ '^#[0-9A-Fa-f]{6}$'", name=op.f('ck_categories_color_hex')),
    sa.CheckConstraint('char_length(btrim(name)) > 0', name=op.f('ck_categories_name_not_blank')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_categories_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_categories')),
    sa.UniqueConstraint('id', 'user_id', 'kind', name='uq_categories_id_user_id_kind'),
    sa.UniqueConstraint('user_id', 'system_key', name='uq_categories_user_id_system_key')
    )
    op.create_index('ux_categories_user_kind_name', 'categories', ['user_id', 'kind', sa.literal_column('lower(name)')], unique=True)
    op.create_table('financial_snapshots',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('currency', sa.CHAR(length=3), nullable=False),
    sa.Column('kind', postgresql.ENUM('manual', 'scheduled', 'scenario_baseline', name='snapshot_kind', create_type=False), nullable=False),
    sa.Column('as_of_date', sa.Date(), nullable=False),
    sa.Column('monthly_income', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('monthly_expenses', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('total_savings', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('total_debt', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('monthly_debt_service', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('state', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('state_schema_version', sa.SmallInteger(), nullable=False),
    sa.Column('builder_version', sa.String(length=32), nullable=False),
    sa.Column('state_hash', sa.CHAR(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("builder_version ~ '^[0-9]+\\.[0-9]+\\.[0-9]+$'", name=op.f('ck_financial_snapshots_builder_version_semver')),
    sa.CheckConstraint("jsonb_typeof(state) = 'object' AND state ?& ARRAY['income','expenses','savings','debts','goals','investments']", name=op.f('ck_financial_snapshots_state_required_keys')),
    sa.CheckConstraint("state_hash ~ '^[0-9a-f]{64}$'", name=op.f('ck_financial_snapshots_state_hash_hex64')),
    sa.CheckConstraint('monthly_debt_service >= 0', name=op.f('ck_financial_snapshots_monthly_debt_service_non_negative')),
    sa.CheckConstraint('monthly_expenses >= 0', name=op.f('ck_financial_snapshots_monthly_expenses_non_negative')),
    sa.CheckConstraint('monthly_income >= 0', name=op.f('ck_financial_snapshots_monthly_income_non_negative')),
    sa.CheckConstraint('state_schema_version > 0', name=op.f('ck_financial_snapshots_state_schema_version_positive')),
    sa.CheckConstraint('total_debt >= 0', name=op.f('ck_financial_snapshots_total_debt_non_negative')),
    sa.CheckConstraint('total_savings >= 0', name=op.f('ck_financial_snapshots_total_savings_non_negative')),
    sa.ForeignKeyConstraint(['currency'], ['currencies.code'], name=op.f('fk_financial_snapshots_currency_currencies')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_financial_snapshots_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_financial_snapshots')),
    sa.UniqueConstraint('id', 'user_id', 'currency', name='uq_financial_snapshots_id_user_id_currency'),
    sa.UniqueConstraint('id', 'user_id', name='uq_financial_snapshots_id_user_id'),
    sa.UniqueConstraint('user_id', 'as_of_date', 'state_hash', name='uq_financial_snapshots_user_asof_hash')
    )
    op.create_index('ix_snapshots_user_asof', 'financial_snapshots', ['user_id', sa.literal_column('as_of_date DESC')], unique=False)
    op.create_table('goals',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('goal_type', postgresql.ENUM('emergency_fund', 'laptop', 'education', 'travel', 'car', 'other', name='goal_type', create_type=False), nullable=False),
    sa.Column('target_amount', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('starting_amount', sa.Numeric(precision=18, scale=4), server_default=sa.text('0'), nullable=False),
    sa.Column('planned_monthly_contribution', sa.Numeric(precision=18, scale=4), server_default=sa.text('0'), nullable=False),
    sa.Column('currency', sa.CHAR(length=3), nullable=False),
    sa.Column('target_date', sa.Date(), nullable=True),
    sa.Column('status', postgresql.ENUM('active', 'completed', 'paused', 'cancelled', name='goal_status', create_type=False), server_default=sa.text("'active'"), nullable=False),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(status = 'completed') = (completed_at IS NOT NULL)", name=op.f('ck_goals_completed_at_matches_status')),
    sa.CheckConstraint('char_length(btrim(name)) > 0', name=op.f('ck_goals_name_not_blank')),
    sa.CheckConstraint('planned_monthly_contribution >= 0', name=op.f('ck_goals_planned_monthly_contribution_non_negative')),
    sa.CheckConstraint('starting_amount >= 0', name=op.f('ck_goals_starting_amount_non_negative')),
    sa.CheckConstraint('target_amount > 0', name=op.f('ck_goals_target_amount_positive')),
    sa.ForeignKeyConstraint(['currency'], ['currencies.code'], name=op.f('fk_goals_currency_currencies')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_goals_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_goals')),
    sa.UniqueConstraint('id', 'user_id', 'currency', name='uq_goals_id_user_id_currency'),
    sa.UniqueConstraint('id', 'user_id', name='uq_goals_id_user_id')
    )
    op.create_index('ix_goals_active_target_date', 'goals', ['user_id', 'target_date'], unique=False, postgresql_where=sa.text("status = 'active'"))
    op.create_index('ix_goals_user_status', 'goals', ['user_id', 'status'], unique=False)
    op.create_table('investment_simulations',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('investment_type', postgresql.ENUM('sip', 'lump_sum', 'sip_plus_lump_sum', name='investment_type', create_type=False), nullable=False),
    sa.Column('currency', sa.CHAR(length=3), nullable=False),
    sa.Column('initial_amount', sa.Numeric(precision=18, scale=4), server_default=sa.text('0'), nullable=False),
    sa.Column('periodic_contribution', sa.Numeric(precision=18, scale=4), server_default=sa.text('0'), nullable=False),
    sa.Column('contribution_frequency', postgresql.ENUM('monthly', 'quarterly', 'half_yearly', 'yearly', name='contribution_frequency', create_type=False), server_default=sa.text("'monthly'"), nullable=False),
    sa.Column('contribution_timing', postgresql.ENUM('start_of_period', 'end_of_period', name='period_timing', create_type=False), server_default=sa.text("'end_of_period'"), nullable=False),
    sa.Column('annual_step_up_rate', sa.Numeric(precision=9, scale=6), server_default=sa.text('0'), nullable=False),
    sa.Column('expected_annual_return', sa.Numeric(precision=9, scale=6), nullable=False),
    sa.Column('alternate_returns', sa.ARRAY(sa.Numeric(precision=9, scale=6)), server_default=sa.text("'{}'"), nullable=False),
    sa.Column('inflation_rate', sa.Numeric(precision=9, scale=6), nullable=False),
    sa.Column('compounding_periods_per_year', sa.SmallInteger(), server_default=sa.text('12'), nullable=False),
    sa.Column('duration_months', sa.Integer(), nullable=False),
    sa.Column('start_date', sa.Date(), nullable=False),
    sa.Column('is_archived', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(investment_type = 'sip' AND periodic_contribution > 0) OR (investment_type = 'lump_sum' AND initial_amount > 0) OR (investment_type = 'sip_plus_lump_sum' AND periodic_contribution > 0 AND initial_amount > 0)", name=op.f('ck_investment_simulations_type_matches_amounts')),
    sa.CheckConstraint('annual_step_up_rate BETWEEN 0 AND 1', name=op.f('ck_investment_simulations_annual_step_up_rate_range')),
    sa.CheckConstraint('cardinality(alternate_returns) <= 5', name=op.f('ck_investment_simulations_alternate_returns_max5')),
    sa.CheckConstraint('char_length(btrim(name)) > 0', name=op.f('ck_investment_simulations_name_not_blank')),
    sa.CheckConstraint('compounding_periods_per_year IN (1,2,4,12,365)', name=op.f('ck_investment_simulations_compounding_periods_allowed')),
    sa.CheckConstraint('duration_months BETWEEN 1 AND 720', name=op.f('ck_investment_simulations_duration_months_range')),
    sa.CheckConstraint('expected_annual_return BETWEEN -1 AND 1', name=op.f('ck_investment_simulations_expected_annual_return_range')),
    sa.CheckConstraint('inflation_rate BETWEEN 0 AND 1', name=op.f('ck_investment_simulations_inflation_rate_range')),
    sa.CheckConstraint('initial_amount >= 0', name=op.f('ck_investment_simulations_initial_amount_non_negative')),
    sa.CheckConstraint('periodic_contribution >= 0', name=op.f('ck_investment_simulations_periodic_contribution_non_negative')),
    sa.ForeignKeyConstraint(['currency'], ['currencies.code'], name=op.f('fk_investment_simulations_currency_currencies')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_investment_simulations_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_investment_simulations')),
    sa.UniqueConstraint('id', 'user_id', 'currency', name='uq_investment_simulations_id_user_id_currency'),
    sa.UniqueConstraint('id', 'user_id', name='uq_investment_simulations_id_user_id')
    )
    op.create_index('ix_invsim_user', 'investment_simulations', ['user_id', 'is_archived', sa.literal_column('updated_at DESC')], unique=False)
    op.create_table('loans',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('loan_type', postgresql.ENUM('education', 'personal', 'vehicle', 'home', 'credit_card', 'other', name='loan_type', create_type=False), nullable=False),
    sa.Column('lender', sa.String(length=100), nullable=True),
    sa.Column('principal', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('currency', sa.CHAR(length=3), nullable=False),
    sa.Column('annual_interest_rate', sa.Numeric(precision=9, scale=6), nullable=False),
    sa.Column('tenure_months', sa.Integer(), nullable=False),
    sa.Column('moratorium_months', sa.Integer(), server_default=sa.text('0'), nullable=False),
    sa.Column('start_date', sa.Date(), nullable=False),
    sa.Column('first_payment_date', sa.Date(), nullable=False),
    sa.Column('emi_amount', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('emi_engine_version', sa.String(length=32), nullable=False),
    sa.Column('status', postgresql.ENUM('active', 'paid_off', 'cancelled', name='loan_status', create_type=False), server_default=sa.text("'active'"), nullable=False),
    sa.Column('closed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(status = 'active') = (closed_at IS NULL)", name=op.f('ck_loans_closed_at_matches_status')),
    sa.CheckConstraint("emi_engine_version ~ '^[0-9]+\\.[0-9]+\\.[0-9]+$'", name=op.f('ck_loans_emi_engine_version_semver')),
    sa.CheckConstraint('annual_interest_rate BETWEEN 0 AND 1', name=op.f('ck_loans_annual_interest_rate_range')),
    sa.CheckConstraint('char_length(btrim(name)) > 0', name=op.f('ck_loans_name_not_blank')),
    sa.CheckConstraint('emi_amount > 0', name=op.f('ck_loans_emi_amount_positive')),
    sa.CheckConstraint('first_payment_date >= start_date', name=op.f('ck_loans_first_payment_after_start')),
    sa.CheckConstraint('moratorium_months BETWEEN 0 AND 120', name=op.f('ck_loans_moratorium_months_range')),
    sa.CheckConstraint('principal > 0', name=op.f('ck_loans_principal_positive')),
    sa.CheckConstraint('tenure_months BETWEEN 1 AND 600', name=op.f('ck_loans_tenure_months_range')),
    sa.ForeignKeyConstraint(['currency'], ['currencies.code'], name=op.f('fk_loans_currency_currencies')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_loans_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_loans')),
    sa.UniqueConstraint('id', 'user_id', 'currency', name='uq_loans_id_user_id_currency'),
    sa.UniqueConstraint('id', 'user_id', name='uq_loans_id_user_id')
    )
    op.create_index('ix_loans_user_status', 'loans', ['user_id', 'status'], unique=False)
    op.create_table('notification_preferences',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('notification_type', postgresql.ENUM('goal_reminder', 'budget_alert', 'emi_reminder', 'monthly_summary', 'system', name='notification_type', create_type=False), nullable=False),
    sa.Column('in_app', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('email', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('lead_days', sa.SmallInteger(), server_default=sa.text('3'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('lead_days BETWEEN 0 AND 30', name=op.f('ck_notification_preferences_lead_days_range')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_notification_preferences_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', 'notification_type', name=op.f('pk_notification_preferences'))
    )
    op.create_table('refresh_tokens',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('family_id', sa.UUID(), nullable=False),
    sa.Column('token_hash', sa.CHAR(length=64), nullable=False),
    sa.Column('issued_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('replaced_by_id', sa.UUID(), nullable=True),
    sa.CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name=op.f('ck_refresh_tokens_token_hash_hex64')),
    sa.CheckConstraint('expires_at > issued_at', name=op.f('ck_refresh_tokens_expiry_after_issue')),
    sa.ForeignKeyConstraint(['replaced_by_id', 'user_id'], ['refresh_tokens.id', 'refresh_tokens.user_id'], name='fk_refresh_tokens_replaced_by', ondelete='SET NULL (replaced_by_id)'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_refresh_tokens_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_refresh_tokens')),
    sa.UniqueConstraint('id', 'user_id', name='uq_refresh_tokens_id_user_id'),
    sa.UniqueConstraint('token_hash', name='uq_refresh_tokens_token_hash')
    )
    op.create_index('ix_refresh_tokens_expires', 'refresh_tokens', ['expires_at'], unique=False)
    op.create_index('ix_refresh_tokens_family', 'refresh_tokens', ['family_id'], unique=False)
    op.create_index('ix_refresh_tokens_replaced', 'refresh_tokens', ['replaced_by_id', 'user_id'], unique=False, postgresql_where=sa.text('replaced_by_id IS NOT NULL'))
    op.create_index('ix_refresh_tokens_user_active', 'refresh_tokens', ['user_id'], unique=False, postgresql_where=sa.text('revoked_at IS NULL'))
    op.create_table('scenarios',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('description', sa.String(length=1000), nullable=True),
    sa.Column('scenario_type', postgresql.ENUM('purchase', 'loan', 'investment_plan', 'education_plan', 'trip_plan', 'savings_change', 'income_change', 'expense_change', 'custom', name='scenario_type', create_type=False), nullable=False),
    sa.Column('currency', sa.CHAR(length=3), nullable=False),
    sa.Column('assumptions_schema_version', sa.SmallInteger(), nullable=False),
    sa.Column('assumptions', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('status', postgresql.ENUM('draft', 'active', 'archived', name='scenario_status', create_type=False), server_default=sa.text("'draft'"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("jsonb_typeof(assumptions) = 'object' AND assumptions ?& ARRAY['horizon_months','starting_balance','monthly_income','monthly_expenses','inflation_rate','expected_annual_return']", name=op.f('ck_scenarios_assumptions_required_keys')),
    sa.CheckConstraint('assumptions_schema_version > 0', name=op.f('ck_scenarios_assumptions_schema_version_positive')),
    sa.CheckConstraint('char_length(btrim(name)) > 0', name=op.f('ck_scenarios_name_not_blank')),
    sa.ForeignKeyConstraint(['currency'], ['currencies.code'], name=op.f('fk_scenarios_currency_currencies')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_scenarios_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_scenarios')),
    sa.UniqueConstraint('id', 'user_id', 'currency', 'scenario_type', name='uq_scenarios_id_user_id_currency_type'),
    sa.UniqueConstraint('id', 'user_id', name='uq_scenarios_id_user_id')
    )
    op.create_index('ix_scenarios_user_status', 'scenarios', ['user_id', 'status', sa.literal_column('updated_at DESC')], unique=False)
    op.create_index('ix_scenarios_user_type', 'scenarios', ['user_id', 'scenario_type'], unique=False)
    op.create_table('user_preferences',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('base_currency', sa.CHAR(length=3), nullable=False),
    sa.Column('locale', sa.String(length=16), nullable=False),
    sa.Column('default_inflation_rate', sa.Numeric(precision=9, scale=6), nullable=False),
    sa.Column('default_expected_return', sa.Numeric(precision=9, scale=6), nullable=False),
    sa.Column('default_horizon_months', sa.Integer(), nullable=False),
    sa.Column('budget_alert_threshold', sa.Numeric(precision=5, scale=4), nullable=False),
    sa.Column('onboarding_completed', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('budget_alert_threshold > 0 AND budget_alert_threshold <= 1', name=op.f('ck_user_preferences_budget_alert_threshold_range')),
    sa.CheckConstraint('default_expected_return BETWEEN -1 AND 1', name=op.f('ck_user_preferences_default_expected_return_range')),
    sa.CheckConstraint('default_horizon_months BETWEEN 1 AND 720', name=op.f('ck_user_preferences_default_horizon_months_range')),
    sa.CheckConstraint('default_inflation_rate BETWEEN 0 AND 1', name=op.f('ck_user_preferences_default_inflation_rate_range')),
    sa.ForeignKeyConstraint(['base_currency'], ['currencies.code'], name=op.f('fk_user_preferences_base_currency_currencies')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_user_preferences_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', name=op.f('pk_user_preferences'))
    )
    op.create_table('user_profiles',
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('full_name', sa.String(length=120), nullable=True),
    sa.Column('display_name', sa.String(length=60), nullable=True),
    sa.Column('country_code', sa.CHAR(length=2), nullable=True),
    sa.Column('timezone', sa.String(length=64), server_default=sa.text("'Asia/Kolkata'"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("country_code ~ '^[A-Z]{2}$'", name=op.f('ck_user_profiles_country_code_format')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_user_profiles_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('user_id', name=op.f('pk_user_profiles'))
    )
    op.create_table('budgets',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('category_id', sa.UUID(), nullable=False),
    sa.Column('category_kind', postgresql.ENUM('income', 'expense', name='txn_kind', create_type=False), server_default=sa.text("'expense'"), nullable=False),
    sa.Column('month', sa.Date(), nullable=False),
    sa.Column('limit_amount', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('currency', sa.CHAR(length=3), nullable=False),
    sa.Column('alert_threshold', sa.Numeric(precision=5, scale=4), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("category_kind = 'expense'", name=op.f('ck_budgets_category_kind_expense')),
    sa.CheckConstraint('EXTRACT(day FROM month) = 1', name=op.f('ck_budgets_month_first_day')),
    sa.CheckConstraint('alert_threshold > 0 AND alert_threshold <= 1', name=op.f('ck_budgets_alert_threshold_range')),
    sa.CheckConstraint('limit_amount > 0', name=op.f('ck_budgets_limit_amount_positive')),
    sa.ForeignKeyConstraint(['category_id', 'user_id', 'category_kind'], ['categories.id', 'categories.user_id', 'categories.kind'], name='fk_budgets_category', deferrable=True, initially='DEFERRED'),
    sa.ForeignKeyConstraint(['currency'], ['currencies.code'], name=op.f('fk_budgets_currency_currencies')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_budgets_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_budgets')),
    sa.UniqueConstraint('id', 'user_id', name='uq_budgets_id_user_id'),
    sa.UniqueConstraint('user_id', 'category_id', 'month', name='uq_budgets_user_id_category_id_month')
    )
    op.create_index('ix_budgets_category_fk', 'budgets', ['category_id', 'user_id'], unique=False)
    op.create_table('financial_projections',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('snapshot_id', sa.UUID(), nullable=False),
    sa.Column('currency', sa.CHAR(length=3), nullable=False),
    sa.Column('kind', postgresql.ENUM('baseline', 'scenario', name='projection_kind', create_type=False), nullable=False),
    sa.Column('horizon_months', sa.Integer(), nullable=False),
    sa.Column('start_date', sa.Date(), nullable=False),
    sa.Column('engine_version', sa.String(length=32), nullable=False),
    sa.Column('assumptions_schema_version', sa.SmallInteger(), nullable=False),
    sa.Column('assumptions', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('inputs_hash', sa.CHAR(length=64), nullable=False),
    sa.Column('final_state', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("engine_version ~ '^[0-9]+\\.[0-9]+\\.[0-9]+$'", name=op.f('ck_financial_projections_engine_version_semver')),
    sa.CheckConstraint("inputs_hash ~ '^[0-9a-f]{64}$'", name=op.f('ck_financial_projections_inputs_hash_hex64')),
    sa.CheckConstraint("jsonb_typeof(assumptions) = 'object'", name=op.f('ck_financial_projections_assumptions_is_object')),
    sa.CheckConstraint("jsonb_typeof(final_state) = 'object'", name=op.f('ck_financial_projections_final_state_is_object')),
    sa.CheckConstraint('assumptions_schema_version > 0', name=op.f('ck_financial_projections_assumptions_schema_version_positive')),
    sa.CheckConstraint('horizon_months BETWEEN 1 AND 720', name=op.f('ck_financial_projections_horizon_months_range')),
    sa.ForeignKeyConstraint(['snapshot_id', 'user_id', 'currency'], ['financial_snapshots.id', 'financial_snapshots.user_id', 'financial_snapshots.currency'], name='fk_financial_projections_snapshot', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_financial_projections')),
    sa.UniqueConstraint('id', 'user_id', 'snapshot_id', name='uq_financial_projections_id_user_id_snapshot'),
    sa.UniqueConstraint('id', 'user_id', name='uq_financial_projections_id_user_id'),
    sa.UniqueConstraint('snapshot_id', 'kind', 'inputs_hash', 'engine_version', name='uq_financial_projections_snapshot_kind_hash_version')
    )
    op.create_table('goal_contributions',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('goal_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('currency', sa.CHAR(length=3), nullable=False),
    sa.Column('kind', postgresql.ENUM('contribution', 'withdrawal', name='goal_entry_kind', create_type=False), server_default=sa.text("'contribution'"), nullable=False),
    sa.Column('amount', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('contribution_date', sa.Date(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('amount > 0', name=op.f('ck_goal_contributions_amount_positive')),
    sa.ForeignKeyConstraint(['goal_id', 'user_id', 'currency'], ['goals.id', 'goals.user_id', 'goals.currency'], name='fk_goal_contributions_goal', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_goal_contributions'))
    )
    op.create_index('ix_goal_contrib_goal_date', 'goal_contributions', ['goal_id', sa.literal_column('contribution_date DESC')], unique=False)
    op.create_index('ix_goal_contrib_user_date', 'goal_contributions', ['user_id', sa.literal_column('contribution_date DESC')], unique=False)
    op.create_table('goal_progress_snapshots',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('goal_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('currency', sa.CHAR(length=3), nullable=False),
    sa.Column('snapshot_date', sa.Date(), nullable=False),
    sa.Column('current_amount', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('target_amount', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('progress_ratio', sa.Numeric(precision=9, scale=6), nullable=False),
    sa.Column('required_monthly_contribution', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('estimated_completion_date', sa.Date(), nullable=True),
    sa.Column('calc_type', postgresql.ENUM('emi', 'loan_amortization', 'present_value', 'future_value', 'compound_interest', 'sip', 'lump_sum', 'inflation_adjusted', 'savings_projection', 'opportunity_cost', 'goal_contribution', 'twin_projection', 'scenario_simulation', 'scenario_comparison', name='calc_type', create_type=False), server_default=sa.text("'goal_contribution'"), nullable=False),
    sa.Column('engine_version', sa.String(length=32), nullable=False),
    sa.Column('assumptions', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('inputs_hash', sa.CHAR(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("calc_type = 'goal_contribution'", name=op.f('ck_goal_progress_snapshots_calc_type_goal_contribution')),
    sa.CheckConstraint("engine_version ~ '^[0-9]+\\.[0-9]+\\.[0-9]+$'", name=op.f('ck_goal_progress_snapshots_engine_version_semver')),
    sa.CheckConstraint("inputs_hash ~ '^[0-9a-f]{64}$'", name=op.f('ck_goal_progress_snapshots_inputs_hash_hex64')),
    sa.CheckConstraint("jsonb_typeof(assumptions) = 'object'", name=op.f('ck_goal_progress_snapshots_assumptions_is_object')),
    sa.CheckConstraint('current_amount >= 0', name=op.f('ck_goal_progress_snapshots_current_amount_non_negative')),
    sa.CheckConstraint('progress_ratio >= 0', name=op.f('ck_goal_progress_snapshots_progress_ratio_non_negative')),
    sa.CheckConstraint('required_monthly_contribution >= 0', name=op.f('ck_goal_progress_snapshots_required_monthly_contribution_non_negative')),
    sa.CheckConstraint('target_amount > 0', name=op.f('ck_goal_progress_snapshots_target_amount_positive')),
    sa.ForeignKeyConstraint(['goal_id', 'user_id', 'currency'], ['goals.id', 'goals.user_id', 'goals.currency'], name='fk_goal_progress_snapshots_goal', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_goal_progress_snapshots')),
    sa.UniqueConstraint('goal_id', 'snapshot_date', 'engine_version', name='uq_goal_snap_goal_date_version')
    )
    op.create_index('ix_goal_snap_user_date', 'goal_progress_snapshots', ['user_id', sa.literal_column('snapshot_date DESC')], unique=False)
    op.create_table('investment_simulation_runs',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('simulation_id', sa.UUID(), nullable=False),
    sa.Column('currency', sa.CHAR(length=3), nullable=False),
    sa.Column('calc_type', postgresql.ENUM('emi', 'loan_amortization', 'present_value', 'future_value', 'compound_interest', 'sip', 'lump_sum', 'inflation_adjusted', 'savings_projection', 'opportunity_cost', 'goal_contribution', 'twin_projection', 'scenario_simulation', 'scenario_comparison', name='calc_type', create_type=False), nullable=False),
    sa.Column('engine_version', sa.String(length=32), nullable=False),
    sa.Column('assumptions_schema_version', sa.SmallInteger(), nullable=False),
    sa.Column('assumptions', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('inputs_hash', sa.CHAR(length=64), nullable=False),
    sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("calc_type IN ('sip','lump_sum','compound_interest','future_value','inflation_adjusted')", name=op.f('ck_investment_simulation_runs_calc_type_allowed')),
    sa.CheckConstraint("engine_version ~ '^[0-9]+\\.[0-9]+\\.[0-9]+$'", name=op.f('ck_investment_simulation_runs_engine_version_semver')),
    sa.CheckConstraint("inputs_hash ~ '^[0-9a-f]{64}$'", name=op.f('ck_investment_simulation_runs_inputs_hash_hex64')),
    sa.CheckConstraint("jsonb_typeof(assumptions) = 'object'", name=op.f('ck_investment_simulation_runs_assumptions_is_object')),
    sa.CheckConstraint("jsonb_typeof(result) = 'object'", name=op.f('ck_investment_simulation_runs_result_is_object')),
    sa.CheckConstraint('assumptions_schema_version > 0', name=op.f('ck_investment_simulation_runs_assumptions_schema_version_positive')),
    sa.ForeignKeyConstraint(['simulation_id', 'user_id', 'currency'], ['investment_simulations.id', 'investment_simulations.user_id', 'investment_simulations.currency'], name='fk_investment_simulation_runs_simulation', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_investment_simulation_runs')),
    sa.UniqueConstraint('id', 'user_id', name='uq_investment_simulation_runs_id_user_id'),
    sa.UniqueConstraint('simulation_id', 'inputs_hash', 'engine_version', name='uq_invsim_runs_sim_hash_version')
    )
    op.create_index('ix_invsim_runs_sim', 'investment_simulation_runs', ['simulation_id', sa.literal_column('created_at DESC')], unique=False)
    op.create_table('loan_schedules',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('loan_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('currency', sa.CHAR(length=3), nullable=False),
    sa.Column('calc_type', postgresql.ENUM('emi', 'loan_amortization', 'present_value', 'future_value', 'compound_interest', 'sip', 'lump_sum', 'inflation_adjusted', 'savings_projection', 'opportunity_cost', 'goal_contribution', 'twin_projection', 'scenario_simulation', 'scenario_comparison', name='calc_type', create_type=False), server_default=sa.text("'loan_amortization'"), nullable=False),
    sa.Column('engine_version', sa.String(length=32), nullable=False),
    sa.Column('assumptions', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('inputs_hash', sa.CHAR(length=64), nullable=False),
    sa.Column('installment_count', sa.Integer(), nullable=False),
    sa.Column('total_interest', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('total_payable', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('is_current', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("calc_type = 'loan_amortization'", name=op.f('ck_loan_schedules_calc_type_loan_amortization')),
    sa.CheckConstraint("engine_version ~ '^[0-9]+\\.[0-9]+\\.[0-9]+$'", name=op.f('ck_loan_schedules_engine_version_semver')),
    sa.CheckConstraint("inputs_hash ~ '^[0-9a-f]{64}$'", name=op.f('ck_loan_schedules_inputs_hash_hex64')),
    sa.CheckConstraint("jsonb_typeof(assumptions) = 'object'", name=op.f('ck_loan_schedules_assumptions_is_object')),
    sa.CheckConstraint('installment_count > 0', name=op.f('ck_loan_schedules_installment_count_positive')),
    sa.CheckConstraint('total_interest >= 0', name=op.f('ck_loan_schedules_total_interest_non_negative')),
    sa.CheckConstraint('total_payable > 0', name=op.f('ck_loan_schedules_total_payable_positive')),
    sa.ForeignKeyConstraint(['loan_id', 'user_id', 'currency'], ['loans.id', 'loans.user_id', 'loans.currency'], name='fk_loan_schedules_loan', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_loan_schedules')),
    sa.UniqueConstraint('id', 'user_id', name='uq_loan_schedules_id_user_id'),
    sa.UniqueConstraint('loan_id', 'inputs_hash', 'engine_version', name='uq_loan_schedules_loan_hash_version')
    )
    op.create_index('ux_loan_schedules_current', 'loan_schedules', ['loan_id'], unique=True, postgresql_where=sa.text('is_current'))
    op.create_table('recurring_rules',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('category_id', sa.UUID(), nullable=False),
    sa.Column('kind', postgresql.ENUM('income', 'expense', name='txn_kind', create_type=False), nullable=False),
    sa.Column('amount', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('currency', sa.CHAR(length=3), nullable=False),
    sa.Column('description', sa.String(length=255), nullable=True),
    sa.Column('frequency', postgresql.ENUM('daily', 'weekly', 'monthly', 'quarterly', 'yearly', name='recurrence_frequency', create_type=False), nullable=False),
    sa.Column('interval_count', sa.SmallInteger(), server_default=sa.text('1'), nullable=False),
    sa.Column('day_of_month', sa.SmallInteger(), nullable=True),
    sa.Column('start_date', sa.Date(), nullable=False),
    sa.Column('end_date', sa.Date(), nullable=True),
    sa.Column('next_run_date', sa.Date(), nullable=True),
    sa.Column('last_run_date', sa.Date(), nullable=True),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('NOT is_active OR next_run_date IS NOT NULL', name=op.f('ck_recurring_rules_active_has_next_run')),
    sa.CheckConstraint('amount > 0', name=op.f('ck_recurring_rules_amount_positive')),
    sa.CheckConstraint('day_of_month BETWEEN 1 AND 31', name=op.f('ck_recurring_rules_day_of_month_range')),
    sa.CheckConstraint('end_date IS NULL OR end_date >= start_date', name=op.f('ck_recurring_rules_end_after_start')),
    sa.CheckConstraint('interval_count BETWEEN 1 AND 60', name=op.f('ck_recurring_rules_interval_count_range')),
    sa.ForeignKeyConstraint(['category_id', 'user_id', 'kind'], ['categories.id', 'categories.user_id', 'categories.kind'], name='fk_recurring_rules_category', deferrable=True, initially='DEFERRED'),
    sa.ForeignKeyConstraint(['currency'], ['currencies.code'], name=op.f('fk_recurring_rules_currency_currencies')),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_recurring_rules_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_recurring_rules')),
    sa.UniqueConstraint('id', 'user_id', name='uq_recurring_rules_id_user_id')
    )
    op.create_index('ix_recurring_rules_category_fk', 'recurring_rules', ['category_id', 'user_id'], unique=False)
    op.create_index('ix_recurring_rules_due', 'recurring_rules', ['next_run_date'], unique=False, postgresql_where=sa.text('is_active'))
    op.create_index('ix_recurring_rules_user', 'recurring_rules', ['user_id'], unique=False)
    op.create_table('scenario_comparisons',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('snapshot_id', sa.UUID(), nullable=False),
    sa.Column('currency', sa.CHAR(length=3), nullable=False),
    sa.Column('horizon_months', sa.Integer(), nullable=False),
    sa.Column('calc_type', postgresql.ENUM('emi', 'loan_amortization', 'present_value', 'future_value', 'compound_interest', 'sip', 'lump_sum', 'inflation_adjusted', 'savings_projection', 'opportunity_cost', 'goal_contribution', 'twin_projection', 'scenario_simulation', 'scenario_comparison', name='calc_type', create_type=False), server_default=sa.text("'scenario_comparison'"), nullable=False),
    sa.Column('engine_version', sa.String(length=32), nullable=False),
    sa.Column('inputs_hash', sa.CHAR(length=64), nullable=False),
    sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("calc_type = 'scenario_comparison'", name=op.f('ck_scenario_comparisons_calc_type_scenario_comparison')),
    sa.CheckConstraint("engine_version ~ '^[0-9]+\\.[0-9]+\\.[0-9]+$'", name=op.f('ck_scenario_comparisons_engine_version_semver')),
    sa.CheckConstraint("inputs_hash ~ '^[0-9a-f]{64}$'", name=op.f('ck_scenario_comparisons_inputs_hash_hex64')),
    sa.CheckConstraint("jsonb_typeof(result) = 'object'", name=op.f('ck_scenario_comparisons_result_is_object')),
    sa.CheckConstraint('char_length(btrim(name)) > 0', name=op.f('ck_scenario_comparisons_name_not_blank')),
    sa.CheckConstraint('horizon_months BETWEEN 1 AND 720', name=op.f('ck_scenario_comparisons_horizon_months_range')),
    sa.ForeignKeyConstraint(['currency'], ['currencies.code'], name=op.f('fk_scenario_comparisons_currency_currencies')),
    sa.ForeignKeyConstraint(['snapshot_id', 'user_id', 'currency'], ['financial_snapshots.id', 'financial_snapshots.user_id', 'financial_snapshots.currency'], name='fk_scenario_comparisons_snapshot', deferrable=True, initially='DEFERRED'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_scenario_comparisons_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_scenario_comparisons')),
    sa.UniqueConstraint('id', 'user_id', 'currency', 'snapshot_id', 'horizon_months', name='uq_scenario_comparisons_comparability_key'),
    sa.UniqueConstraint('id', 'user_id', name='uq_scenario_comparisons_id_user_id'),
    sa.UniqueConstraint('user_id', 'inputs_hash', 'engine_version', name='uq_scenario_comparisons_user_hash_version')
    )
    op.create_index('ix_comparisons_snapshot', 'scenario_comparisons', ['snapshot_id', 'user_id'], unique=False)
    op.create_index('ix_comparisons_user_created', 'scenario_comparisons', ['user_id', sa.literal_column('created_at DESC')], unique=False)
    op.create_table('financial_projection_points',
    sa.Column('projection_id', sa.UUID(), nullable=False),
    sa.Column('month_index', sa.SmallInteger(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('period_date', sa.Date(), nullable=False),
    sa.Column('income', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('expenses', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('debt_service', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('decision_cash_flow', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('net_cash_flow', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('savings_balance', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('debt_balance', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('investment_value', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('net_worth', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('savings_rate', sa.Numeric(precision=9, scale=6), nullable=True),
    sa.Column('extra', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.CheckConstraint("jsonb_typeof(extra) = 'object'", name=op.f('ck_financial_projection_points_extra_is_object')),
    sa.CheckConstraint('debt_balance >= 0', name=op.f('ck_financial_projection_points_debt_balance_non_negative')),
    sa.CheckConstraint('debt_service >= 0', name=op.f('ck_financial_projection_points_debt_service_non_negative')),
    sa.CheckConstraint('expenses >= 0', name=op.f('ck_financial_projection_points_expenses_non_negative')),
    sa.CheckConstraint('income >= 0', name=op.f('ck_financial_projection_points_income_non_negative')),
    sa.CheckConstraint('investment_value >= 0', name=op.f('ck_financial_projection_points_investment_value_non_negative')),
    sa.CheckConstraint('month_index BETWEEN 0 AND 720', name=op.f('ck_financial_projection_points_month_index_range')),
    sa.ForeignKeyConstraint(['projection_id', 'user_id'], ['financial_projections.id', 'financial_projections.user_id'], name='fk_financial_projection_points_projection', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('projection_id', 'month_index', name=op.f('pk_financial_projection_points'))
    )
    op.create_index('ix_proj_points_user', 'financial_projection_points', ['user_id', 'projection_id'], unique=False)
    op.create_table('loan_schedule_items',
    sa.Column('schedule_id', sa.UUID(), nullable=False),
    sa.Column('installment_number', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('due_date', sa.Date(), nullable=False),
    sa.Column('opening_balance', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('payment_amount', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('principal_component', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('interest_component', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('closing_balance', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.CheckConstraint('closing_balance >= 0', name=op.f('ck_loan_schedule_items_closing_balance_non_negative')),
    sa.CheckConstraint('installment_number >= 1', name=op.f('ck_loan_schedule_items_installment_number_min')),
    sa.CheckConstraint('interest_component >= 0', name=op.f('ck_loan_schedule_items_interest_component_non_negative')),
    sa.CheckConstraint('opening_balance >= 0', name=op.f('ck_loan_schedule_items_opening_balance_non_negative')),
    sa.CheckConstraint('payment_amount = principal_component + interest_component', name=op.f('ck_loan_schedule_items_payment_equals_components')),
    sa.CheckConstraint('payment_amount >= 0', name=op.f('ck_loan_schedule_items_payment_amount_non_negative')),
    sa.CheckConstraint('principal_component >= 0', name=op.f('ck_loan_schedule_items_principal_component_non_negative')),
    sa.ForeignKeyConstraint(['schedule_id', 'user_id'], ['loan_schedules.id', 'loan_schedules.user_id'], name='fk_loan_schedule_items_schedule', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('schedule_id', 'installment_number', name=op.f('pk_loan_schedule_items'))
    )
    op.create_index('ix_loan_items_user_due', 'loan_schedule_items', ['user_id', 'due_date'], unique=False)
    op.create_table('notifications',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('type', postgresql.ENUM('goal_reminder', 'budget_alert', 'emi_reminder', 'monthly_summary', 'system', name='notification_type', create_type=False), nullable=False),
    sa.Column('severity', postgresql.ENUM('info', 'warning', 'critical', name='notification_severity', create_type=False), server_default=sa.text("'info'"), nullable=False),
    sa.Column('title', sa.String(length=150), nullable=False),
    sa.Column('body', sa.String(length=1000), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('due_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('read_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('dismissed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('dedupe_key', sa.String(length=200), nullable=False),
    sa.Column('goal_id', sa.UUID(), nullable=True),
    sa.Column('loan_id', sa.UUID(), nullable=True),
    sa.Column('budget_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("jsonb_typeof(payload) = 'object'", name=op.f('ck_notifications_payload_is_object')),
    sa.ForeignKeyConstraint(['budget_id', 'user_id'], ['budgets.id', 'budgets.user_id'], name='fk_notifications_budget', ondelete='SET NULL (budget_id)'),
    sa.ForeignKeyConstraint(['goal_id', 'user_id'], ['goals.id', 'goals.user_id'], name='fk_notifications_goal', ondelete='SET NULL (goal_id)'),
    sa.ForeignKeyConstraint(['loan_id', 'user_id'], ['loans.id', 'loans.user_id'], name='fk_notifications_loan', ondelete='SET NULL (loan_id)'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_notifications_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_notifications')),
    sa.UniqueConstraint('id', 'user_id', name='uq_notifications_id_user_id'),
    sa.UniqueConstraint('user_id', 'dedupe_key', name='uq_notifications_user_id_dedupe_key')
    )
    op.create_index('ix_notif_budget', 'notifications', ['budget_id', 'user_id'], unique=False, postgresql_where=sa.text('budget_id IS NOT NULL'))
    op.create_index('ix_notif_goal', 'notifications', ['goal_id', 'user_id'], unique=False, postgresql_where=sa.text('goal_id IS NOT NULL'))
    op.create_index('ix_notif_loan', 'notifications', ['loan_id', 'user_id'], unique=False, postgresql_where=sa.text('loan_id IS NOT NULL'))
    op.create_index('ix_notif_unread', 'notifications', ['user_id', sa.literal_column('due_at DESC')], unique=False, postgresql_where=sa.text('read_at IS NULL AND dismissed_at IS NULL'))
    op.create_index('ix_notif_user_created', 'notifications', ['user_id', sa.literal_column('created_at DESC')], unique=False)
    op.create_table('scenario_runs',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('scenario_id', sa.UUID(), nullable=False),
    sa.Column('snapshot_id', sa.UUID(), nullable=False),
    sa.Column('currency', sa.CHAR(length=3), nullable=False),
    sa.Column('scenario_type', postgresql.ENUM('purchase', 'loan', 'investment_plan', 'education_plan', 'trip_plan', 'savings_change', 'income_change', 'expense_change', 'custom', name='scenario_type', create_type=False), nullable=False),
    sa.Column('calc_type', postgresql.ENUM('emi', 'loan_amortization', 'present_value', 'future_value', 'compound_interest', 'sip', 'lump_sum', 'inflation_adjusted', 'savings_projection', 'opportunity_cost', 'goal_contribution', 'twin_projection', 'scenario_simulation', 'scenario_comparison', name='calc_type', create_type=False), server_default=sa.text("'scenario_simulation'"), nullable=False),
    sa.Column('horizon_months', sa.Integer(), nullable=False),
    sa.Column('start_date', sa.Date(), nullable=False),
    sa.Column('engine_version', sa.String(length=32), nullable=False),
    sa.Column('assumptions_schema_version', sa.SmallInteger(), nullable=False),
    sa.Column('assumptions', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('inputs_hash', sa.CHAR(length=64), nullable=False),
    sa.Column('baseline_projection_id', sa.UUID(), nullable=False),
    sa.Column('scenario_projection_id', sa.UUID(), nullable=False),
    sa.Column('result', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("assumptions ->> 'horizon_months' = horizon_months::text", name=op.f('ck_scenario_runs_horizon_matches_assumptions')),
    sa.CheckConstraint("calc_type = 'scenario_simulation'", name=op.f('ck_scenario_runs_calc_type_scenario_simulation')),
    sa.CheckConstraint("engine_version ~ '^[0-9]+\\.[0-9]+\\.[0-9]+$'", name=op.f('ck_scenario_runs_engine_version_semver')),
    sa.CheckConstraint("inputs_hash ~ '^[0-9a-f]{64}$'", name=op.f('ck_scenario_runs_inputs_hash_hex64')),
    sa.CheckConstraint("jsonb_typeof(assumptions) = 'object'", name=op.f('ck_scenario_runs_assumptions_is_object')),
    sa.CheckConstraint("jsonb_typeof(result) = 'object'", name=op.f('ck_scenario_runs_result_is_object')),
    sa.CheckConstraint('assumptions_schema_version > 0', name=op.f('ck_scenario_runs_assumptions_schema_version_positive')),
    sa.CheckConstraint('baseline_projection_id <> scenario_projection_id', name=op.f('ck_scenario_runs_projections_distinct')),
    sa.CheckConstraint('horizon_months BETWEEN 1 AND 720', name=op.f('ck_scenario_runs_horizon_months_range')),
    sa.ForeignKeyConstraint(['baseline_projection_id', 'user_id', 'snapshot_id'], ['financial_projections.id', 'financial_projections.user_id', 'financial_projections.snapshot_id'], name='fk_scenario_runs_baseline_projection', deferrable=True, initially='DEFERRED'),
    sa.ForeignKeyConstraint(['scenario_id', 'user_id', 'currency', 'scenario_type'], ['scenarios.id', 'scenarios.user_id', 'scenarios.currency', 'scenarios.scenario_type'], name='fk_scenario_runs_scenario', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['scenario_projection_id', 'user_id', 'snapshot_id'], ['financial_projections.id', 'financial_projections.user_id', 'financial_projections.snapshot_id'], name='fk_scenario_runs_scenario_projection', deferrable=True, initially='DEFERRED'),
    sa.ForeignKeyConstraint(['snapshot_id', 'user_id', 'currency'], ['financial_snapshots.id', 'financial_snapshots.user_id', 'financial_snapshots.currency'], name='fk_scenario_runs_snapshot', deferrable=True, initially='DEFERRED'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_scenario_runs')),
    sa.UniqueConstraint('id', 'user_id', 'currency', 'snapshot_id', 'horizon_months', name='uq_scenario_runs_comparability_key'),
    sa.UniqueConstraint('id', 'user_id', name='uq_scenario_runs_id_user_id'),
    sa.UniqueConstraint('scenario_id', 'inputs_hash', 'engine_version', name='uq_scenario_runs_scenario_hash_version')
    )
    op.create_index('ix_runs_proj_base', 'scenario_runs', ['baseline_projection_id'], unique=False)
    op.create_index('ix_runs_proj_scen', 'scenario_runs', ['scenario_projection_id'], unique=False)
    op.create_index('ix_runs_scenario', 'scenario_runs', ['scenario_id', sa.literal_column('created_at DESC')], unique=False)
    op.create_index('ix_runs_snapshot', 'scenario_runs', ['snapshot_id', 'user_id'], unique=False)
    op.create_index('ix_runs_user_created', 'scenario_runs', ['user_id', sa.literal_column('created_at DESC')], unique=False)
    op.create_table('transactions',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('category_id', sa.UUID(), nullable=False),
    sa.Column('kind', postgresql.ENUM('income', 'expense', name='txn_kind', create_type=False), nullable=False),
    sa.Column('amount', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('currency', sa.CHAR(length=3), nullable=False),
    sa.Column('transaction_date', sa.Date(), nullable=False),
    sa.Column('description', sa.String(length=255), nullable=True),
    sa.Column('source', postgresql.ENUM('manual', 'recurring', 'import', name='txn_source', create_type=False), server_default=sa.text("'manual'"), nullable=False),
    sa.Column('recurring_rule_id', sa.UUID(), nullable=True),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("transaction_date BETWEEN DATE '1990-01-01' AND DATE '2100-12-31'", name=op.f('ck_transactions_transaction_date_range')),
    sa.CheckConstraint('amount > 0', name=op.f('ck_transactions_amount_positive')),
    sa.ForeignKeyConstraint(['category_id', 'user_id', 'kind'], ['categories.id', 'categories.user_id', 'categories.kind'], name='fk_transactions_category', deferrable=True, initially='DEFERRED'),
    sa.ForeignKeyConstraint(['currency'], ['currencies.code'], name=op.f('fk_transactions_currency_currencies')),
    sa.ForeignKeyConstraint(['recurring_rule_id', 'user_id'], ['recurring_rules.id', 'recurring_rules.user_id'], name='fk_transactions_recurring_rule', ondelete='SET NULL (recurring_rule_id)'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_transactions_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_transactions')),
    sa.UniqueConstraint('id', 'user_id', name='uq_transactions_id_user_id')
    )
    op.create_index('ix_txn_category_fk', 'transactions', ['category_id', 'user_id'], unique=False)
    op.create_index('ix_txn_desc_trgm', 'transactions', ['user_id', 'description'], unique=False, postgresql_using='gin', postgresql_ops={'description': 'gin_trgm_ops'}, postgresql_where=sa.text('deleted_at IS NULL'))
    op.create_index('ix_txn_user_cat_date', 'transactions', ['user_id', 'category_id', sa.literal_column('transaction_date DESC')], unique=False, postgresql_where=sa.text('deleted_at IS NULL'))
    op.create_index('ix_txn_user_date', 'transactions', ['user_id', sa.literal_column('transaction_date DESC')], unique=False, postgresql_where=sa.text('deleted_at IS NULL'))
    op.create_index('ix_txn_user_kind_date', 'transactions', ['user_id', 'kind', sa.literal_column('transaction_date DESC')], unique=False, postgresql_where=sa.text('deleted_at IS NULL'))
    op.create_index('ux_txn_recurring_instance', 'transactions', ['recurring_rule_id', 'transaction_date'], unique=True, postgresql_where=sa.text('recurring_rule_id IS NOT NULL'))
    op.create_table('ai_analyses',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('task', postgresql.ENUM('explain_scenario', 'compare_scenarios', 'explain_investment', 'explain_concept', 'spending_patterns', 'monthly_summary', 'goal_insights', name='ai_task', create_type=False), nullable=False),
    sa.Column('scenario_run_id', sa.UUID(), nullable=True),
    sa.Column('comparison_id', sa.UUID(), nullable=True),
    sa.Column('investment_simulation_run_id', sa.UUID(), nullable=True),
    sa.Column('period_start', sa.Date(), nullable=True),
    sa.Column('period_end', sa.Date(), nullable=True),
    sa.Column('context_schema_version', sa.SmallInteger(), nullable=False),
    sa.Column('context', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('context_hash', sa.CHAR(length=64), nullable=False),
    sa.Column('prompt_version', sa.String(length=32), nullable=False),
    sa.Column('provider', sa.String(length=40), nullable=False),
    sa.Column('model', sa.String(length=100), nullable=False),
    sa.Column('user_question', sa.String(length=500), nullable=True),
    sa.Column('output', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('validation_status', postgresql.ENUM('passed', 'failed_fallback_used', 'skipped', name='ai_validation_status', create_type=False), nullable=False),
    sa.Column('validation_details', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    sa.Column('input_tokens', sa.Integer(), nullable=True),
    sa.Column('output_tokens', sa.Integer(), nullable=True),
    sa.Column('latency_ms', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("context_hash ~ '^[0-9a-f]{64}$'", name=op.f('ck_ai_analyses_context_hash_hex64')),
    sa.CheckConstraint("jsonb_typeof(context) = 'object'", name=op.f('ck_ai_analyses_context_is_object')),
    sa.CheckConstraint("jsonb_typeof(output) = 'object'", name=op.f('ck_ai_analyses_output_is_object')),
    sa.CheckConstraint("task <> 'compare_scenarios' OR comparison_id IS NOT NULL", name=op.f('ck_ai_analyses_compare_needs_comparison')),
    sa.CheckConstraint("task <> 'explain_investment' OR investment_simulation_run_id IS NOT NULL", name=op.f('ck_ai_analyses_explain_investment_needs_run')),
    sa.CheckConstraint("task <> 'explain_scenario' OR scenario_run_id IS NOT NULL", name=op.f('ck_ai_analyses_explain_scenario_needs_run')),
    sa.CheckConstraint('context_schema_version > 0', name=op.f('ck_ai_analyses_context_schema_version_positive')),
    sa.CheckConstraint('input_tokens >= 0', name=op.f('ck_ai_analyses_input_tokens_non_negative')),
    sa.CheckConstraint('latency_ms >= 0', name=op.f('ck_ai_analyses_latency_ms_non_negative')),
    sa.CheckConstraint('num_nonnulls(scenario_run_id, comparison_id, investment_simulation_run_id) <= 1', name=op.f('ck_ai_analyses_single_source')),
    sa.CheckConstraint('output_tokens >= 0', name=op.f('ck_ai_analyses_output_tokens_non_negative')),
    sa.CheckConstraint('period_end IS NULL OR period_start IS NOT NULL AND period_end >= period_start', name=op.f('ck_ai_analyses_period_order')),
    sa.ForeignKeyConstraint(['comparison_id', 'user_id'], ['scenario_comparisons.id', 'scenario_comparisons.user_id'], name='fk_ai_analyses_comparison', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['investment_simulation_run_id', 'user_id'], ['investment_simulation_runs.id', 'investment_simulation_runs.user_id'], name='fk_ai_analyses_investment_simulation_run', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['scenario_run_id', 'user_id'], ['scenario_runs.id', 'scenario_runs.user_id'], name='fk_ai_analyses_scenario_run', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_ai_analyses_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ai_analyses'))
    )
    op.create_index('ix_ai_cache_lookup', 'ai_analyses', ['user_id', 'task', 'context_hash', 'prompt_version', 'model'], unique=False)
    op.create_index('ix_ai_comparison', 'ai_analyses', ['comparison_id', 'user_id'], unique=False, postgresql_where=sa.text('comparison_id IS NOT NULL'))
    op.create_index('ix_ai_invrun', 'ai_analyses', ['investment_simulation_run_id', 'user_id'], unique=False, postgresql_where=sa.text('investment_simulation_run_id IS NOT NULL'))
    op.create_index('ix_ai_run', 'ai_analyses', ['scenario_run_id', 'user_id'], unique=False, postgresql_where=sa.text('scenario_run_id IS NOT NULL'))
    op.create_index('ix_ai_user_created', 'ai_analyses', ['user_id', sa.literal_column('created_at DESC')], unique=False)
    op.create_table('loan_payments',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('loan_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('currency', sa.CHAR(length=3), nullable=False),
    sa.Column('payment_type', postgresql.ENUM('emi', 'prepayment', 'fee', name='loan_payment_type', create_type=False), server_default=sa.text("'emi'"), nullable=False),
    sa.Column('installment_number', sa.Integer(), nullable=True),
    sa.Column('payment_date', sa.Date(), nullable=False),
    sa.Column('amount', sa.Numeric(precision=18, scale=4), nullable=False),
    sa.Column('principal_paid', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('interest_paid', sa.Numeric(precision=18, scale=4), nullable=True),
    sa.Column('transaction_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("payment_type = 'emi' OR installment_number IS NULL", name=op.f('ck_loan_payments_installment_only_for_emi')),
    sa.CheckConstraint('COALESCE(principal_paid,0) + COALESCE(interest_paid,0) <= amount', name=op.f('ck_loan_payments_components_within_amount')),
    sa.CheckConstraint('amount > 0', name=op.f('ck_loan_payments_amount_positive')),
    sa.CheckConstraint('installment_number >= 1', name=op.f('ck_loan_payments_installment_number_min')),
    sa.CheckConstraint('interest_paid >= 0', name=op.f('ck_loan_payments_interest_paid_non_negative')),
    sa.CheckConstraint('principal_paid >= 0', name=op.f('ck_loan_payments_principal_paid_non_negative')),
    sa.ForeignKeyConstraint(['loan_id', 'user_id', 'currency'], ['loans.id', 'loans.user_id', 'loans.currency'], name='fk_loan_payments_loan', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['transaction_id', 'user_id'], ['transactions.id', 'transactions.user_id'], name='fk_loan_payments_transaction', ondelete='SET NULL (transaction_id)'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_loan_payments'))
    )
    op.create_index('ix_loan_payments_loan_date', 'loan_payments', ['loan_id', sa.literal_column('payment_date DESC')], unique=False)
    op.create_index('ix_loan_payments_txn', 'loan_payments', ['transaction_id', 'user_id'], unique=False, postgresql_where=sa.text('transaction_id IS NOT NULL'))
    op.create_index('ix_loan_payments_user_date', 'loan_payments', ['user_id', sa.literal_column('payment_date DESC')], unique=False)
    op.create_index('ux_loan_payments_installment', 'loan_payments', ['loan_id', 'installment_number'], unique=True, postgresql_where=sa.text("payment_type = 'emi' AND installment_number IS NOT NULL"))
    op.create_table('notification_deliveries',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('notification_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('channel', postgresql.ENUM('in_app', 'email', name='delivery_channel', create_type=False), nullable=False),
    sa.Column('status', postgresql.ENUM('pending', 'sent', 'failed', 'skipped', name='delivery_status', create_type=False), server_default=sa.text("'pending'"), nullable=False),
    sa.Column('attempts', sa.SmallInteger(), server_default=sa.text('0'), nullable=False),
    sa.Column('last_error', sa.String(length=255), nullable=True),
    sa.Column('scheduled_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('sent_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(status = 'sent') = (sent_at IS NOT NULL)", name=op.f('ck_notification_deliveries_sent_at_matches_status')),
    sa.CheckConstraint('attempts >= 0', name=op.f('ck_notification_deliveries_attempts_non_negative')),
    sa.ForeignKeyConstraint(['notification_id', 'user_id'], ['notifications.id', 'notifications.user_id'], name='fk_notification_deliveries_notification', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_notification_deliveries')),
    sa.UniqueConstraint('notification_id', 'channel', name='uq_notification_deliveries_notification_channel')
    )
    op.create_index('ix_deliveries_pending', 'notification_deliveries', ['scheduled_at'], unique=False, postgresql_where=sa.text("status = 'pending'"))
    op.create_index('ix_deliveries_user', 'notification_deliveries', ['user_id'], unique=False)
    op.create_table('scenario_comparison_items',
    sa.Column('comparison_id', sa.UUID(), nullable=False),
    sa.Column('position', sa.SmallInteger(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('currency', sa.CHAR(length=3), nullable=False),
    sa.Column('snapshot_id', sa.UUID(), nullable=False),
    sa.Column('horizon_months', sa.Integer(), nullable=False),
    sa.Column('scenario_run_id', sa.UUID(), nullable=False),
    sa.Column('label', sa.String(length=60), nullable=False),
    sa.CheckConstraint('position BETWEEN 1 AND 3', name=op.f('ck_scenario_comparison_items_position_range')),
    sa.ForeignKeyConstraint(['comparison_id', 'user_id', 'currency', 'snapshot_id', 'horizon_months'], ['scenario_comparisons.id', 'scenario_comparisons.user_id', 'scenario_comparisons.currency', 'scenario_comparisons.snapshot_id', 'scenario_comparisons.horizon_months'], name='fk_scenario_comparison_items_comparison', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['scenario_run_id', 'user_id', 'currency', 'snapshot_id', 'horizon_months'], ['scenario_runs.id', 'scenario_runs.user_id', 'scenario_runs.currency', 'scenario_runs.snapshot_id', 'scenario_runs.horizon_months'], name='fk_scenario_comparison_items_run', deferrable=True, initially='DEFERRED'),
    sa.PrimaryKeyConstraint('comparison_id', 'position', name=op.f('pk_scenario_comparison_items')),
    sa.UniqueConstraint('comparison_id', 'scenario_run_id', name='uq_scenario_comparison_items_comparison_run')
    )
    op.create_index('ix_comp_items_run', 'scenario_comparison_items', ['scenario_run_id'], unique=False)

    op.execute(CURRENCY_SEED)
    for _name, ddl in VIEWS:
        op.execute(ddl)
    op.execute(UPDATED_AT_TRIGGERS)
    for ddl in TRIGGERS:
        op.execute(ddl)


def downgrade() -> None:
    for name, _ddl in reversed(VIEWS):
        op.execute(f"DROP VIEW IF EXISTS {name}")
    op.drop_index('ix_comp_items_run', table_name='scenario_comparison_items')
    op.drop_table('scenario_comparison_items')
    op.drop_index('ix_deliveries_user', table_name='notification_deliveries')
    op.drop_index('ix_deliveries_pending', table_name='notification_deliveries', postgresql_where=sa.text("status = 'pending'"))
    op.drop_table('notification_deliveries')
    op.drop_index('ux_loan_payments_installment', table_name='loan_payments', postgresql_where=sa.text("payment_type = 'emi' AND installment_number IS NOT NULL"))
    op.drop_index('ix_loan_payments_user_date', table_name='loan_payments')
    op.drop_index('ix_loan_payments_txn', table_name='loan_payments', postgresql_where=sa.text('transaction_id IS NOT NULL'))
    op.drop_index('ix_loan_payments_loan_date', table_name='loan_payments')
    op.drop_table('loan_payments')
    op.drop_index('ix_ai_user_created', table_name='ai_analyses')
    op.drop_index('ix_ai_run', table_name='ai_analyses', postgresql_where=sa.text('scenario_run_id IS NOT NULL'))
    op.drop_index('ix_ai_invrun', table_name='ai_analyses', postgresql_where=sa.text('investment_simulation_run_id IS NOT NULL'))
    op.drop_index('ix_ai_comparison', table_name='ai_analyses', postgresql_where=sa.text('comparison_id IS NOT NULL'))
    op.drop_index('ix_ai_cache_lookup', table_name='ai_analyses')
    op.drop_table('ai_analyses')
    op.drop_index('ux_txn_recurring_instance', table_name='transactions', postgresql_where=sa.text('recurring_rule_id IS NOT NULL'))
    op.drop_index('ix_txn_user_kind_date', table_name='transactions', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_index('ix_txn_user_date', table_name='transactions', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_index('ix_txn_user_cat_date', table_name='transactions', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_index('ix_txn_desc_trgm', table_name='transactions', postgresql_using='gin', postgresql_ops={'description': 'gin_trgm_ops'}, postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_index('ix_txn_category_fk', table_name='transactions')
    op.drop_table('transactions')
    op.drop_index('ix_runs_user_created', table_name='scenario_runs')
    op.drop_index('ix_runs_snapshot', table_name='scenario_runs')
    op.drop_index('ix_runs_scenario', table_name='scenario_runs')
    op.drop_index('ix_runs_proj_scen', table_name='scenario_runs')
    op.drop_index('ix_runs_proj_base', table_name='scenario_runs')
    op.drop_table('scenario_runs')
    op.drop_index('ix_notif_user_created', table_name='notifications')
    op.drop_index('ix_notif_unread', table_name='notifications', postgresql_where=sa.text('read_at IS NULL AND dismissed_at IS NULL'))
    op.drop_index('ix_notif_loan', table_name='notifications', postgresql_where=sa.text('loan_id IS NOT NULL'))
    op.drop_index('ix_notif_goal', table_name='notifications', postgresql_where=sa.text('goal_id IS NOT NULL'))
    op.drop_index('ix_notif_budget', table_name='notifications', postgresql_where=sa.text('budget_id IS NOT NULL'))
    op.drop_table('notifications')
    op.drop_index('ix_loan_items_user_due', table_name='loan_schedule_items')
    op.drop_table('loan_schedule_items')
    op.drop_index('ix_proj_points_user', table_name='financial_projection_points')
    op.drop_table('financial_projection_points')
    op.drop_index('ix_comparisons_user_created', table_name='scenario_comparisons')
    op.drop_index('ix_comparisons_snapshot', table_name='scenario_comparisons')
    op.drop_table('scenario_comparisons')
    op.drop_index('ix_recurring_rules_user', table_name='recurring_rules')
    op.drop_index('ix_recurring_rules_due', table_name='recurring_rules', postgresql_where=sa.text('is_active'))
    op.drop_index('ix_recurring_rules_category_fk', table_name='recurring_rules')
    op.drop_table('recurring_rules')
    op.drop_index('ux_loan_schedules_current', table_name='loan_schedules', postgresql_where=sa.text('is_current'))
    op.drop_table('loan_schedules')
    op.drop_index('ix_invsim_runs_sim', table_name='investment_simulation_runs')
    op.drop_table('investment_simulation_runs')
    op.drop_index('ix_goal_snap_user_date', table_name='goal_progress_snapshots')
    op.drop_table('goal_progress_snapshots')
    op.drop_index('ix_goal_contrib_user_date', table_name='goal_contributions')
    op.drop_index('ix_goal_contrib_goal_date', table_name='goal_contributions')
    op.drop_table('goal_contributions')
    op.drop_table('financial_projections')
    op.drop_index('ix_budgets_category_fk', table_name='budgets')
    op.drop_table('budgets')
    op.drop_table('user_profiles')
    op.drop_table('user_preferences')
    op.drop_index('ix_scenarios_user_type', table_name='scenarios')
    op.drop_index('ix_scenarios_user_status', table_name='scenarios')
    op.drop_table('scenarios')
    op.drop_index('ix_refresh_tokens_user_active', table_name='refresh_tokens', postgresql_where=sa.text('revoked_at IS NULL'))
    op.drop_index('ix_refresh_tokens_replaced', table_name='refresh_tokens', postgresql_where=sa.text('replaced_by_id IS NOT NULL'))
    op.drop_index('ix_refresh_tokens_family', table_name='refresh_tokens')
    op.drop_index('ix_refresh_tokens_expires', table_name='refresh_tokens')
    op.drop_table('refresh_tokens')
    op.drop_table('notification_preferences')
    op.drop_index('ix_loans_user_status', table_name='loans')
    op.drop_table('loans')
    op.drop_index('ix_invsim_user', table_name='investment_simulations')
    op.drop_table('investment_simulations')
    op.drop_index('ix_goals_user_status', table_name='goals')
    op.drop_index('ix_goals_active_target_date', table_name='goals', postgresql_where=sa.text("status = 'active'"))
    op.drop_table('goals')
    op.drop_index('ix_snapshots_user_asof', table_name='financial_snapshots')
    op.drop_table('financial_snapshots')
    op.drop_index('ux_categories_user_kind_name', table_name='categories')
    op.drop_table('categories')
    op.drop_index('ix_auth_tokens_user_purpose', table_name='auth_tokens')
    op.drop_table('auth_tokens')
    op.drop_index('ix_audit_user_created', table_name='audit_log')
    op.drop_index('ix_audit_entity', table_name='audit_log')
    op.drop_index('ix_audit_action', table_name='audit_log')
    op.drop_table('audit_log')
    op.drop_table('users')
    op.drop_table('currencies')

    for name, _ddl in reversed(FUNCTIONS):
        op.execute(f"DROP FUNCTION IF EXISTS {name}() CASCADE")
    for name, _ddl in reversed(ENUMS):
        op.execute(f"DROP TYPE IF EXISTS {name}")
