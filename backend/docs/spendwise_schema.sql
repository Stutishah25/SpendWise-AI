-- =====================================================================
-- SpendWise AI: PostgreSQL schema (reference rendering, v1.1: validated against PostgreSQL 16)
-- Requires PostgreSQL 15+ (uses  ON DELETE SET NULL (column)  on composite FKs).
-- The authoritative source at implementation time is the Alembic migration
-- history; this file is the reviewed design in executable form.
-- Stores INPUTS, METADATA and RESULTS only. No financial formulas live here.
-- =====================================================================
BEGIN;

CREATE EXTENSION IF NOT EXISTS citext;      -- case-insensitive email
CREATE EXTENSION IF NOT EXISTS pg_trgm;     -- transaction text search
CREATE EXTENSION IF NOT EXISTS btree_gin;   -- (user_id, trigram) composite GIN

-- ---------------------------------------------------------------------
-- 1. ENUMS (closed, stable vocabularies; extend with ALTER TYPE ... ADD VALUE)
-- ---------------------------------------------------------------------
CREATE TYPE txn_kind               AS ENUM ('income','expense');
CREATE TYPE txn_source             AS ENUM ('manual','recurring','import');
CREATE TYPE recurrence_frequency   AS ENUM ('daily','weekly','monthly','quarterly','yearly');
CREATE TYPE goal_type              AS ENUM ('emergency_fund','laptop','education','travel','car','other');
CREATE TYPE goal_status            AS ENUM ('active','completed','paused','cancelled');
CREATE TYPE goal_entry_kind        AS ENUM ('contribution','withdrawal');
CREATE TYPE loan_type              AS ENUM ('education','personal','vehicle','home','credit_card','other');
CREATE TYPE loan_status            AS ENUM ('active','paid_off','cancelled');
CREATE TYPE loan_payment_type      AS ENUM ('emi','prepayment','fee');
CREATE TYPE contribution_frequency AS ENUM ('monthly','quarterly','half_yearly','yearly');
CREATE TYPE period_timing          AS ENUM ('start_of_period','end_of_period');
CREATE TYPE investment_type        AS ENUM ('sip','lump_sum','sip_plus_lump_sum');
CREATE TYPE scenario_type          AS ENUM ('purchase','loan','investment_plan','education_plan',
                                            'trip_plan','savings_change','income_change','expense_change','custom');
CREATE TYPE scenario_status        AS ENUM ('draft','active','archived');
CREATE TYPE calc_type              AS ENUM ('emi','loan_amortization','present_value','future_value',
                                            'compound_interest','sip','lump_sum','inflation_adjusted',
                                            'savings_projection','opportunity_cost','goal_contribution',
                                            'twin_projection','scenario_simulation','scenario_comparison');
CREATE TYPE snapshot_kind          AS ENUM ('manual','scheduled','scenario_baseline');
CREATE TYPE projection_kind        AS ENUM ('baseline','scenario');
CREATE TYPE ai_task                AS ENUM ('explain_scenario','compare_scenarios','explain_investment',
                                            'explain_concept','spending_patterns','monthly_summary','goal_insights');
CREATE TYPE ai_validation_status   AS ENUM ('passed','failed_fallback_used','skipped');
CREATE TYPE notification_type      AS ENUM ('goal_reminder','budget_alert','emi_reminder','monthly_summary','system');
CREATE TYPE notification_severity  AS ENUM ('info','warning','critical');
CREATE TYPE delivery_channel       AS ENUM ('in_app','email');
CREATE TYPE delivery_status        AS ENUM ('pending','sent','failed','skipped');
CREATE TYPE auth_token_purpose     AS ENUM ('email_verification','password_reset');
CREATE TYPE audit_outcome          AS ENUM ('success','denied','failure');

-- ---------------------------------------------------------------------
-- 2. TRIGGER FUNCTIONS
-- ---------------------------------------------------------------------
CREATE FUNCTION set_updated_at() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN NEW.updated_at := now(); RETURN NEW; END $$;

-- Blocks UPDATEs to audited result tables. Trigger args = columns that MAY change.
CREATE FUNCTION enforce_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE allowed text[] := COALESCE(TG_ARGV, ARRAY[]::text[]);   -- TG_ARGV is NULL (not '{}') when no args are given
BEGIN
  IF (to_jsonb(NEW) - allowed) IS DISTINCT FROM (to_jsonb(OLD) - allowed) THEN
    RAISE EXCEPTION 'table % is immutable (mutable columns: %)', TG_TABLE_NAME,
      COALESCE(NULLIF(array_to_string(allowed, ', '), ''), 'none')
      USING ERRCODE = 'integrity_constraint_violation';
  END IF;
  RETURN NEW;
END $$;

-- User-entered amounts may not have more decimals than the currency's minor units
-- (e.g. no 10.005 INR). Engine OUTPUT columns keep NUMERIC(18,4) precision and are not checked.
CREATE FUNCTION enforce_minor_units() RETURNS trigger LANGUAGE plpgsql AS $$
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
END $$;

-- ---------------------------------------------------------------------
-- 3. REFERENCE DATA
-- ---------------------------------------------------------------------
CREATE TABLE currencies (
  code           char(3) PRIMARY KEY CHECK (code ~ '^[A-Z]{3}$'),
  name           varchar(60)  NOT NULL,
  symbol         varchar(8)   NOT NULL,           -- presentation metadata ONLY
  minor_units    smallint     NOT NULL CHECK (minor_units BETWEEN 0 AND 4),
  default_locale varchar(16)  NOT NULL,
  is_enabled     boolean      NOT NULL DEFAULT false
);
INSERT INTO currencies (code, name, symbol, minor_units, default_locale, is_enabled) VALUES
  ('INR','Indian Rupee','₹',2,'en-IN',true),
  ('USD','US Dollar','$',2,'en-US',false),
  ('EUR','Euro','€',2,'de-DE',false),
  ('GBP','Pound Sterling','£',2,'en-GB',false);

-- ---------------------------------------------------------------------
-- 4. USERS & AUTHENTICATION
-- ---------------------------------------------------------------------
CREATE TABLE users (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email              citext NOT NULL UNIQUE CHECK (char_length(email) BETWEEN 3 AND 254),
  password_hash      text NOT NULL,                       -- argon2id; never plaintext
  is_active          boolean NOT NULL DEFAULT true,
  email_verified_at  timestamptz,
  last_login_at      timestamptz,
  failed_login_count smallint NOT NULL DEFAULT 0 CHECK (failed_login_count >= 0),
  locked_until       timestamptz,
  password_changed_at timestamptz NOT NULL DEFAULT now(),
  deleted_at         timestamptz,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE user_profiles (
  user_id      uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  full_name    varchar(120),
  display_name varchar(60),
  country_code char(2) CHECK (country_code ~ '^[A-Z]{2}$'),
  timezone     varchar(64) NOT NULL DEFAULT 'Asia/Kolkata',
  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE user_preferences (
  user_id                 uuid PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
  base_currency           char(3) NOT NULL REFERENCES currencies(code),
  locale                  varchar(16) NOT NULL,
  default_inflation_rate  numeric(9,6) NOT NULL CHECK (default_inflation_rate BETWEEN 0 AND 1),
  default_expected_return numeric(9,6) NOT NULL CHECK (default_expected_return BETWEEN -1 AND 1),
  default_horizon_months  integer NOT NULL CHECK (default_horizon_months BETWEEN 1 AND 720),
  budget_alert_threshold  numeric(5,4) NOT NULL CHECK (budget_alert_threshold > 0 AND budget_alert_threshold <= 1),
  onboarding_completed    boolean NOT NULL DEFAULT false,
  created_at              timestamptz NOT NULL DEFAULT now(),
  updated_at              timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE refresh_tokens (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id        uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  family_id      uuid NOT NULL,                           -- rotation family (reuse detection)
  token_hash     char(64) NOT NULL UNIQUE CHECK (token_hash ~ '^[0-9a-f]{64}$'),
  issued_at      timestamptz NOT NULL DEFAULT now(),
  expires_at     timestamptz NOT NULL,
  last_used_at   timestamptz,
  revoked_at     timestamptz,
  replaced_by_id uuid,
  UNIQUE (id, user_id),
  FOREIGN KEY (replaced_by_id, user_id) REFERENCES refresh_tokens (id, user_id) ON DELETE SET NULL (replaced_by_id),
  CHECK (expires_at > issued_at)
);
CREATE INDEX ix_refresh_tokens_replaced ON refresh_tokens (replaced_by_id, user_id) WHERE replaced_by_id IS NOT NULL;
CREATE INDEX ix_refresh_tokens_user_active ON refresh_tokens (user_id) WHERE revoked_at IS NULL;
CREATE INDEX ix_refresh_tokens_family      ON refresh_tokens (family_id);
CREATE INDEX ix_refresh_tokens_expires     ON refresh_tokens (expires_at);

CREATE TABLE auth_tokens (                                -- email verification / password reset
  id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  purpose    auth_token_purpose NOT NULL,
  token_hash char(64) NOT NULL UNIQUE CHECK (token_hash ~ '^[0-9a-f]{64}$'),
  expires_at timestamptz NOT NULL,
  used_at    timestamptz,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_auth_tokens_user_purpose ON auth_tokens (user_id, purpose);

-- NOTE: "protective" foreign keys (NO ACTION: a category / snapshot / projection / run that is still in use
-- cannot be deleted) are DEFERRABLE INITIALLY DEFERRED. Without that, deleting a USER can fail because the
-- cascade may remove a parent before the children that reference it. The in-use check then runs at COMMIT.
-- ---------------------------------------------------------------------
-- 5. FINANCIAL DATA: categories, recurring rules, transactions, budgets
--    Ownership pattern: every child carries user_id and references its parent
--    through a COMPOSITE FK (parent_id, user_id) -> parent(id, user_id).
-- ---------------------------------------------------------------------
CREATE TABLE categories (                                 -- per-user (seeded from defaults at signup)
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  kind        txn_kind NOT NULL,
  name        varchar(60) NOT NULL CHECK (char_length(btrim(name)) > 0),
  system_key  varchar(40),                                -- stable key for seeded defaults
  color       char(7) CHECK (color ~ '^#[0-9A-Fa-f]{6}$'),
  icon        varchar(40),
  is_archived boolean NOT NULL DEFAULT false,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (id, user_id, kind),
  UNIQUE (user_id, system_key)
);
CREATE UNIQUE INDEX ux_categories_user_kind_name ON categories (user_id, kind, lower(name));

CREATE TABLE recurring_rules (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  category_id   uuid NOT NULL,
  kind          txn_kind NOT NULL,
  amount        numeric(18,4) NOT NULL CHECK (amount > 0),
  currency      char(3) NOT NULL REFERENCES currencies(code),
  description   varchar(255),
  frequency     recurrence_frequency NOT NULL,
  interval_count smallint NOT NULL DEFAULT 1 CHECK (interval_count BETWEEN 1 AND 60),
  day_of_month  smallint CHECK (day_of_month BETWEEN 1 AND 31),
  start_date    date NOT NULL,
  end_date      date,
  next_run_date date,
  last_run_date date,
  is_active     boolean NOT NULL DEFAULT true,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (id, user_id),
  FOREIGN KEY (category_id, user_id, kind) REFERENCES categories (id, user_id, kind) DEFERRABLE INITIALLY DEFERRED,
  CHECK (end_date IS NULL OR end_date >= start_date),
  CHECK (NOT is_active OR next_run_date IS NOT NULL)
);
CREATE INDEX ix_recurring_rules_due  ON recurring_rules (next_run_date) WHERE is_active;
CREATE INDEX ix_recurring_rules_user ON recurring_rules (user_id);
CREATE INDEX ix_recurring_rules_category_fk ON recurring_rules (category_id, user_id);

CREATE TABLE transactions (                               -- income AND expenses (kind)
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id           uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  category_id       uuid NOT NULL,
  kind              txn_kind NOT NULL,
  amount            numeric(18,4) NOT NULL CHECK (amount > 0),
  currency          char(3) NOT NULL REFERENCES currencies(code),
  transaction_date  date NOT NULL CHECK (transaction_date BETWEEN DATE '1990-01-01' AND DATE '2100-12-31'),
  description       varchar(255),
  source            txn_source NOT NULL DEFAULT 'manual',
  recurring_rule_id uuid,
  deleted_at        timestamptz,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  UNIQUE (id, user_id),
  FOREIGN KEY (category_id, user_id, kind) REFERENCES categories (id, user_id, kind) DEFERRABLE INITIALLY DEFERRED,  -- type must match category
  FOREIGN KEY (recurring_rule_id, user_id) REFERENCES recurring_rules (id, user_id)
    ON DELETE SET NULL (recurring_rule_id)
);
CREATE INDEX ix_txn_user_date      ON transactions (user_id, transaction_date DESC) WHERE deleted_at IS NULL;
CREATE INDEX ix_txn_user_kind_date ON transactions (user_id, kind, transaction_date DESC) WHERE deleted_at IS NULL;
CREATE INDEX ix_txn_user_cat_date  ON transactions (user_id, category_id, transaction_date DESC) WHERE deleted_at IS NULL;
CREATE INDEX ix_txn_category_fk    ON transactions (category_id, user_id);
CREATE INDEX ix_txn_desc_trgm      ON transactions USING gin (user_id, description gin_trgm_ops) WHERE deleted_at IS NULL;
CREATE UNIQUE INDEX ux_txn_recurring_instance ON transactions (recurring_rule_id, transaction_date)
  WHERE recurring_rule_id IS NOT NULL;                    -- idempotent recurring generation

CREATE TABLE budgets (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id         uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  category_id     uuid NOT NULL,
  category_kind   txn_kind NOT NULL DEFAULT 'expense' CHECK (category_kind = 'expense'),
  month           date NOT NULL CHECK (EXTRACT(day FROM month) = 1),   -- first day of month
  limit_amount    numeric(18,4) NOT NULL CHECK (limit_amount > 0),
  currency        char(3) NOT NULL REFERENCES currencies(code),
  alert_threshold numeric(5,4) CHECK (alert_threshold > 0 AND alert_threshold <= 1),  -- NULL = use preference
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (id, user_id),
  UNIQUE (user_id, category_id, month),
  FOREIGN KEY (category_id, user_id, category_kind) REFERENCES categories (id, user_id, kind) DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX ix_budgets_category_fk ON budgets (category_id, user_id);

-- ---------------------------------------------------------------------
-- 6. GOALS
-- ---------------------------------------------------------------------
CREATE TABLE goals (
  id                           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id                      uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name                         varchar(120) NOT NULL CHECK (char_length(btrim(name)) > 0),
  goal_type                    goal_type NOT NULL,
  target_amount                numeric(18,4) NOT NULL CHECK (target_amount > 0),
  starting_amount              numeric(18,4) NOT NULL DEFAULT 0 CHECK (starting_amount >= 0),
  planned_monthly_contribution numeric(18,4) NOT NULL DEFAULT 0 CHECK (planned_monthly_contribution >= 0),
  currency                     char(3) NOT NULL REFERENCES currencies(code),
  target_date                  date,
  status                       goal_status NOT NULL DEFAULT 'active',
  completed_at                 timestamptz,
  created_at                   timestamptz NOT NULL DEFAULT now(),
  updated_at                   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (id, user_id),
  UNIQUE (id, user_id, currency),
  CHECK ((status = 'completed') = (completed_at IS NOT NULL))
);
CREATE INDEX ix_goals_user_status ON goals (user_id, status);
CREATE INDEX ix_goals_active_target_date ON goals (user_id, target_date) WHERE status = 'active';

CREATE TABLE goal_contributions (                         -- ledger of deposits/withdrawals
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  goal_id           uuid NOT NULL,
  user_id           uuid NOT NULL,
  currency          char(3) NOT NULL,
  kind              goal_entry_kind NOT NULL DEFAULT 'contribution',
  amount            numeric(18,4) NOT NULL CHECK (amount > 0),
  contribution_date date NOT NULL,
  created_at        timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (goal_id, user_id, currency) REFERENCES goals (id, user_id, currency) ON DELETE CASCADE
);
CREATE INDEX ix_goal_contrib_goal_date ON goal_contributions (goal_id, contribution_date DESC);
CREATE INDEX ix_goal_contrib_user_date ON goal_contributions (user_id, contribution_date DESC);

CREATE TABLE goal_progress_snapshots (                    -- immutable periodic engine outputs
  id                            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  goal_id                       uuid NOT NULL,
  user_id                       uuid NOT NULL,
  currency                      char(3) NOT NULL,
  snapshot_date                 date NOT NULL,
  current_amount                numeric(18,4) NOT NULL CHECK (current_amount >= 0),
  target_amount                 numeric(18,4) NOT NULL CHECK (target_amount > 0),
  progress_ratio                numeric(9,6)  NOT NULL CHECK (progress_ratio >= 0),
  required_monthly_contribution numeric(18,4) CHECK (required_monthly_contribution >= 0),
  estimated_completion_date     date,
  calc_type                     calc_type NOT NULL DEFAULT 'goal_contribution' CHECK (calc_type = 'goal_contribution'),
  engine_version                varchar(32) NOT NULL CHECK (engine_version ~ '^[0-9]+\.[0-9]+\.[0-9]+$'),
  assumptions                   jsonb NOT NULL CHECK (jsonb_typeof(assumptions) = 'object'),
  inputs_hash                   char(64) NOT NULL CHECK (inputs_hash ~ '^[0-9a-f]{64}$'),
  created_at                    timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (goal_id, user_id, currency) REFERENCES goals (id, user_id, currency) ON DELETE CASCADE,
  UNIQUE (goal_id, snapshot_date, engine_version)
);
CREATE INDEX ix_goal_snap_user_date ON goal_progress_snapshots (user_id, snapshot_date DESC);

-- ---------------------------------------------------------------------
-- 7. LOANS (actual loans only; hypothetical loans live in scenario assumptions)
-- ---------------------------------------------------------------------
CREATE TABLE loans (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id              uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name                 varchar(120) NOT NULL CHECK (char_length(btrim(name)) > 0),
  loan_type            loan_type NOT NULL,
  lender               varchar(100),
  principal            numeric(18,4) NOT NULL CHECK (principal > 0),
  currency             char(3) NOT NULL REFERENCES currencies(code),
  annual_interest_rate numeric(9,6) NOT NULL CHECK (annual_interest_rate BETWEEN 0 AND 1),
  tenure_months        integer NOT NULL CHECK (tenure_months BETWEEN 1 AND 600),
  moratorium_months    integer NOT NULL DEFAULT 0 CHECK (moratorium_months BETWEEN 0 AND 120),
  start_date           date NOT NULL,
  first_payment_date   date NOT NULL,
  emi_amount           numeric(18,4) NOT NULL CHECK (emi_amount > 0),       -- engine output at creation
  emi_engine_version   varchar(32) NOT NULL CHECK (emi_engine_version ~ '^[0-9]+\.[0-9]+\.[0-9]+$'),
  status               loan_status NOT NULL DEFAULT 'active',
  closed_at            timestamptz,
  created_at           timestamptz NOT NULL DEFAULT now(),
  updated_at           timestamptz NOT NULL DEFAULT now(),
  UNIQUE (id, user_id),
  UNIQUE (id, user_id, currency),
  CHECK (first_payment_date >= start_date),
  CHECK ((status = 'active') = (closed_at IS NULL))
);
CREATE INDEX ix_loans_user_status ON loans (user_id, status);

CREATE TABLE loan_schedules (                             -- persisted amortization (header)
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  loan_id        uuid NOT NULL,
  user_id        uuid NOT NULL,
  currency       char(3) NOT NULL,
  calc_type      calc_type NOT NULL DEFAULT 'loan_amortization' CHECK (calc_type = 'loan_amortization'),
  engine_version varchar(32) NOT NULL CHECK (engine_version ~ '^[0-9]+\.[0-9]+\.[0-9]+$'),
  assumptions    jsonb NOT NULL CHECK (jsonb_typeof(assumptions) = 'object'),
  inputs_hash    char(64) NOT NULL CHECK (inputs_hash ~ '^[0-9a-f]{64}$'),
  installment_count integer NOT NULL CHECK (installment_count > 0),
  total_interest numeric(18,4) NOT NULL CHECK (total_interest >= 0),
  total_payable  numeric(18,4) NOT NULL CHECK (total_payable > 0),
  is_current     boolean NOT NULL DEFAULT true,
  created_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (id, user_id),
  UNIQUE (loan_id, inputs_hash, engine_version),
  FOREIGN KEY (loan_id, user_id, currency) REFERENCES loans (id, user_id, currency) ON DELETE CASCADE
);
CREATE UNIQUE INDEX ux_loan_schedules_current ON loan_schedules (loan_id) WHERE is_current;

CREATE TABLE loan_schedule_items (
  schedule_id         uuid NOT NULL,
  installment_number  integer NOT NULL CHECK (installment_number >= 1),
  user_id             uuid NOT NULL,
  due_date            date NOT NULL,
  opening_balance     numeric(18,4) NOT NULL CHECK (opening_balance >= 0),
  payment_amount      numeric(18,4) NOT NULL CHECK (payment_amount >= 0),
  principal_component numeric(18,4) NOT NULL CHECK (principal_component >= 0),
  interest_component  numeric(18,4) NOT NULL CHECK (interest_component >= 0),
  closing_balance     numeric(18,4) NOT NULL CHECK (closing_balance >= 0),
  PRIMARY KEY (schedule_id, installment_number),
  FOREIGN KEY (schedule_id, user_id) REFERENCES loan_schedules (id, user_id) ON DELETE CASCADE,
  CHECK (payment_amount = principal_component + interest_component)   -- integrity of stored output
);
CREATE INDEX ix_loan_items_user_due ON loan_schedule_items (user_id, due_date);

CREATE TABLE loan_payments (                              -- payments actually made
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  loan_id            uuid NOT NULL,
  user_id            uuid NOT NULL,
  currency           char(3) NOT NULL,
  payment_type       loan_payment_type NOT NULL DEFAULT 'emi',
  installment_number integer CHECK (installment_number >= 1),
  payment_date       date NOT NULL,
  amount             numeric(18,4) NOT NULL CHECK (amount > 0),
  principal_paid     numeric(18,4) CHECK (principal_paid >= 0),
  interest_paid      numeric(18,4) CHECK (interest_paid >= 0),
  transaction_id     uuid,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (loan_id, user_id, currency) REFERENCES loans (id, user_id, currency) ON DELETE CASCADE,
  FOREIGN KEY (transaction_id, user_id) REFERENCES transactions (id, user_id)
    ON DELETE SET NULL (transaction_id),
  CHECK (payment_type = 'emi' OR installment_number IS NULL),
  CHECK (COALESCE(principal_paid,0) + COALESCE(interest_paid,0) <= amount)
);
CREATE UNIQUE INDEX ux_loan_payments_installment ON loan_payments (loan_id, installment_number)
  WHERE payment_type = 'emi' AND installment_number IS NOT NULL;
CREATE INDEX ix_loan_payments_loan_date ON loan_payments (loan_id, payment_date DESC);
CREATE INDEX ix_loan_payments_user_date ON loan_payments (user_id, payment_date DESC);
CREATE INDEX ix_loan_payments_txn ON loan_payments (transaction_id, user_id) WHERE transaction_id IS NOT NULL;

-- ---------------------------------------------------------------------
-- 8. INVESTMENT SIMULATIONS (educational; not holdings)
-- ---------------------------------------------------------------------
CREATE TABLE investment_simulations (                     -- definition + explicit assumptions
  id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id                  uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name                     varchar(120) NOT NULL CHECK (char_length(btrim(name)) > 0),
  investment_type          investment_type NOT NULL,
  currency                 char(3) NOT NULL REFERENCES currencies(code),
  initial_amount           numeric(18,4) NOT NULL DEFAULT 0 CHECK (initial_amount >= 0),
  periodic_contribution    numeric(18,4) NOT NULL DEFAULT 0 CHECK (periodic_contribution >= 0),
  contribution_frequency   contribution_frequency NOT NULL DEFAULT 'monthly',
  contribution_timing      period_timing NOT NULL DEFAULT 'end_of_period',
  annual_step_up_rate      numeric(9,6) NOT NULL DEFAULT 0 CHECK (annual_step_up_rate BETWEEN 0 AND 1),
  expected_annual_return   numeric(9,6) NOT NULL CHECK (expected_annual_return BETWEEN -1 AND 1),
  alternate_returns        numeric(9,6)[] NOT NULL DEFAULT '{}' CHECK (cardinality(alternate_returns) <= 5),
  inflation_rate           numeric(9,6) NOT NULL CHECK (inflation_rate BETWEEN 0 AND 1),
  compounding_periods_per_year smallint NOT NULL DEFAULT 12 CHECK (compounding_periods_per_year IN (1,2,4,12,365)),
  duration_months          integer NOT NULL CHECK (duration_months BETWEEN 1 AND 720),
  start_date               date NOT NULL,
  is_archived              boolean NOT NULL DEFAULT false,
  created_at               timestamptz NOT NULL DEFAULT now(),
  updated_at               timestamptz NOT NULL DEFAULT now(),
  UNIQUE (id, user_id),
  UNIQUE (id, user_id, currency),
  CHECK ((investment_type = 'sip'               AND periodic_contribution > 0)
      OR (investment_type = 'lump_sum'          AND initial_amount > 0)
      OR (investment_type = 'sip_plus_lump_sum' AND periodic_contribution > 0 AND initial_amount > 0))
);
CREATE INDEX ix_invsim_user ON investment_simulations (user_id, is_archived, updated_at DESC);

CREATE TABLE investment_simulation_runs (                 -- immutable results
  id                        uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id                   uuid NOT NULL,
  simulation_id             uuid NOT NULL,
  currency                  char(3) NOT NULL,
  calc_type                 calc_type NOT NULL CHECK (calc_type IN ('sip','lump_sum','compound_interest','future_value','inflation_adjusted')),
  engine_version            varchar(32) NOT NULL CHECK (engine_version ~ '^[0-9]+\.[0-9]+\.[0-9]+$'),
  assumptions_schema_version smallint NOT NULL CHECK (assumptions_schema_version > 0),
  assumptions               jsonb NOT NULL CHECK (jsonb_typeof(assumptions) = 'object'),
  inputs_hash               char(64) NOT NULL CHECK (inputs_hash ~ '^[0-9a-f]{64}$'),
  result                    jsonb NOT NULL CHECK (jsonb_typeof(result) = 'object'),   -- totals, per-return variants, series
  created_at                timestamptz NOT NULL DEFAULT now(),
  UNIQUE (id, user_id),
  UNIQUE (simulation_id, inputs_hash, engine_version),
  FOREIGN KEY (simulation_id, user_id, currency) REFERENCES investment_simulations (id, user_id, currency) ON DELETE CASCADE
);
CREATE INDEX ix_invsim_runs_sim ON investment_simulation_runs (simulation_id, created_at DESC);

-- ---------------------------------------------------------------------
-- 9. DIGITAL TWIN: snapshots and projections
-- ---------------------------------------------------------------------
CREATE TABLE financial_snapshots (                        -- immutable "Current Financial State"
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id              uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  currency             char(3) NOT NULL REFERENCES currencies(code),
  kind                 snapshot_kind NOT NULL,
  as_of_date           date NOT NULL,
  monthly_income       numeric(18,4) NOT NULL CHECK (monthly_income >= 0),
  monthly_expenses     numeric(18,4) NOT NULL CHECK (monthly_expenses >= 0),
  total_savings        numeric(18,4) NOT NULL CHECK (total_savings >= 0),
  total_debt           numeric(18,4) NOT NULL CHECK (total_debt >= 0),
  monthly_debt_service numeric(18,4) NOT NULL CHECK (monthly_debt_service >= 0),
  state                jsonb NOT NULL CHECK (jsonb_typeof(state) = 'object'
                         AND state ?& ARRAY['income','expenses','savings','debts','goals','investments']),
  state_schema_version smallint NOT NULL CHECK (state_schema_version > 0),
  builder_version      varchar(32) NOT NULL CHECK (builder_version ~ '^[0-9]+\.[0-9]+\.[0-9]+$'),
  state_hash           char(64) NOT NULL CHECK (state_hash ~ '^[0-9a-f]{64}$'),
  created_at           timestamptz NOT NULL DEFAULT now(),
  UNIQUE (id, user_id),
  UNIQUE (id, user_id, currency),
  UNIQUE (user_id, as_of_date, state_hash)
);
CREATE INDEX ix_snapshots_user_asof ON financial_snapshots (user_id, as_of_date DESC);

CREATE TABLE financial_projections (                      -- baseline or scenario projection header
  id                         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id                    uuid NOT NULL,
  snapshot_id                uuid NOT NULL,
  currency                   char(3) NOT NULL,
  kind                       projection_kind NOT NULL,
  horizon_months             integer NOT NULL CHECK (horizon_months BETWEEN 1 AND 720),
  start_date                 date NOT NULL,
  engine_version             varchar(32) NOT NULL CHECK (engine_version ~ '^[0-9]+\.[0-9]+\.[0-9]+$'),
  assumptions_schema_version smallint NOT NULL CHECK (assumptions_schema_version > 0),
  assumptions                jsonb NOT NULL CHECK (jsonb_typeof(assumptions) = 'object'),
  inputs_hash                char(64) NOT NULL CHECK (inputs_hash ~ '^[0-9a-f]{64}$'),
  final_state                jsonb NOT NULL CHECK (jsonb_typeof(final_state) = 'object'),
  created_at                 timestamptz NOT NULL DEFAULT now(),
  UNIQUE (id, user_id),
  UNIQUE (id, user_id, snapshot_id),
  UNIQUE (snapshot_id, kind, inputs_hash, engine_version),  -- baseline reuse / dedupe
  FOREIGN KEY (snapshot_id, user_id, currency) REFERENCES financial_snapshots (id, user_id, currency) ON DELETE CASCADE
);

CREATE TABLE financial_projection_points (                -- month-by-month projected financial state
  projection_id       uuid NOT NULL,
  month_index         smallint NOT NULL CHECK (month_index BETWEEN 0 AND 720),
  user_id             uuid NOT NULL,
  period_date         date NOT NULL,
  income              numeric(18,4) NOT NULL CHECK (income >= 0),
  expenses            numeric(18,4) NOT NULL CHECK (expenses >= 0),
  debt_service        numeric(18,4) NOT NULL CHECK (debt_service >= 0),
  decision_cash_flow  numeric(18,4) NOT NULL,             -- signed
  net_cash_flow       numeric(18,4) NOT NULL,             -- signed
  savings_balance     numeric(18,4) NOT NULL,             -- signed (shortfalls visible)
  debt_balance        numeric(18,4) NOT NULL CHECK (debt_balance >= 0),
  investment_value    numeric(18,4) NOT NULL CHECK (investment_value >= 0),
  net_worth           numeric(18,4) NOT NULL,
  savings_rate        numeric(9,6),                       -- NULL when income = 0
  extra               jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(extra) = 'object'),  -- e.g. per-goal balances
  PRIMARY KEY (projection_id, month_index),
  FOREIGN KEY (projection_id, user_id) REFERENCES financial_projections (id, user_id) ON DELETE CASCADE
);
CREATE INDEX ix_proj_points_user ON financial_projection_points (user_id, projection_id);

-- ---------------------------------------------------------------------
-- 10. SCENARIOS
-- ---------------------------------------------------------------------
CREATE TABLE scenarios (                                  -- editable definition
  id                         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id                    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name                       varchar(120) NOT NULL CHECK (char_length(btrim(name)) > 0),
  description                varchar(1000),
  scenario_type              scenario_type NOT NULL,
  currency                   char(3) NOT NULL REFERENCES currencies(code),
  assumptions_schema_version smallint NOT NULL CHECK (assumptions_schema_version > 0),
  assumptions                jsonb NOT NULL CHECK (jsonb_typeof(assumptions) = 'object'
                               AND assumptions ?& ARRAY['horizon_months','starting_balance','monthly_income',
                                                        'monthly_expenses','inflation_rate','expected_annual_return']),
  status                     scenario_status NOT NULL DEFAULT 'draft',
  created_at                 timestamptz NOT NULL DEFAULT now(),
  updated_at                 timestamptz NOT NULL DEFAULT now(),
  UNIQUE (id, user_id),
  UNIQUE (id, user_id, currency, scenario_type)   -- lets runs pin the type they were created under
);
CREATE INDEX ix_scenarios_user_status ON scenarios (user_id, status, updated_at DESC);
CREATE INDEX ix_scenarios_user_type   ON scenarios (user_id, scenario_type);

CREATE TABLE scenario_runs (                              -- immutable, reproducible result
  id                         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id                    uuid NOT NULL,
  scenario_id                uuid NOT NULL,
  snapshot_id                uuid NOT NULL,
  currency                   char(3) NOT NULL,
  scenario_type              scenario_type NOT NULL,
  calc_type                  calc_type NOT NULL DEFAULT 'scenario_simulation' CHECK (calc_type = 'scenario_simulation'),
  horizon_months             integer NOT NULL CHECK (horizon_months BETWEEN 1 AND 720),
  start_date                 date NOT NULL,
  engine_version             varchar(32) NOT NULL CHECK (engine_version ~ '^[0-9]+\.[0-9]+\.[0-9]+$'),
  assumptions_schema_version smallint NOT NULL CHECK (assumptions_schema_version > 0),
  assumptions                jsonb NOT NULL CHECK (jsonb_typeof(assumptions) = 'object'),   -- frozen full copy
  inputs_hash                char(64) NOT NULL CHECK (inputs_hash ~ '^[0-9a-f]{64}$'),      -- snapshot + assumptions + version
  baseline_projection_id     uuid NOT NULL,
  scenario_projection_id     uuid NOT NULL,
  result                     jsonb NOT NULL CHECK (jsonb_typeof(result) = 'object'),        -- metrics, deltas, goal effects, notes
  created_at                 timestamptz NOT NULL DEFAULT now(),
  UNIQUE (id, user_id),
  UNIQUE (id, user_id, currency, snapshot_id, horizon_months),   -- "comparability key"
  UNIQUE (scenario_id, inputs_hash, engine_version),             -- deterministic: same inputs -> same run
  CHECK (baseline_projection_id <> scenario_projection_id),
  CHECK (assumptions ->> 'horizon_months' = horizon_months::text),          -- typed column == frozen assumption
  FOREIGN KEY (scenario_id, user_id, currency, scenario_type) REFERENCES scenarios (id, user_id, currency, scenario_type) ON DELETE CASCADE,
  FOREIGN KEY (snapshot_id, user_id, currency) REFERENCES financial_snapshots (id, user_id, currency) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (baseline_projection_id, user_id, snapshot_id) REFERENCES financial_projections (id, user_id, snapshot_id) DEFERRABLE INITIALLY DEFERRED,
  FOREIGN KEY (scenario_projection_id, user_id, snapshot_id) REFERENCES financial_projections (id, user_id, snapshot_id) DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX ix_runs_user_created ON scenario_runs (user_id, created_at DESC);
CREATE INDEX ix_runs_scenario     ON scenario_runs (scenario_id, created_at DESC);
CREATE INDEX ix_runs_snapshot     ON scenario_runs (snapshot_id, user_id);
CREATE INDEX ix_runs_proj_base    ON scenario_runs (baseline_projection_id);
CREATE INDEX ix_runs_proj_scen    ON scenario_runs (scenario_projection_id);

-- A run must pair a 'baseline' projection with a 'scenario' projection of its own horizon.
CREATE FUNCTION check_run_projections() RETURNS trigger LANGUAGE plpgsql AS $$
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
END $$;
CREATE TRIGGER trg_scenario_runs_check BEFORE INSERT ON scenario_runs
  FOR EACH ROW EXECUTE FUNCTION check_run_projections();

-- Deleting a run removes comparisons that include it (they would otherwise be left with < 2 items) ...
CREATE FUNCTION scenario_run_before_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  DELETE FROM scenario_comparisons c USING scenario_comparison_items i
   WHERE i.scenario_run_id = OLD.id AND i.user_id = OLD.user_id AND c.id = i.comparison_id AND c.user_id = i.user_id;
  RETURN OLD;
END $$;
CREATE TRIGGER trg_scenario_runs_before_delete BEFORE DELETE ON scenario_runs
  FOR EACH ROW EXECUTE FUNCTION scenario_run_before_delete();

-- ... and projections no longer referenced by any run (projections can be shared between runs).
CREATE FUNCTION scenario_run_after_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  DELETE FROM financial_projections p
   WHERE p.user_id = OLD.user_id
     AND p.id IN (OLD.baseline_projection_id, OLD.scenario_projection_id)
     AND NOT EXISTS (SELECT 1 FROM scenario_runs r
                      WHERE r.baseline_projection_id = p.id OR r.scenario_projection_id = p.id);
  RETURN NULL;
END $$;
CREATE TRIGGER trg_scenario_runs_after_delete AFTER DELETE ON scenario_runs
  FOR EACH ROW EXECUTE FUNCTION scenario_run_after_delete();

CREATE TABLE scenario_comparisons (                       -- comparison of 2-3 runs
  id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id             uuid NOT NULL,
  name                varchar(120) NOT NULL CHECK (char_length(btrim(name)) > 0),
  snapshot_id         uuid NOT NULL,
  currency            char(3) NOT NULL,
  horizon_months      integer NOT NULL CHECK (horizon_months BETWEEN 1 AND 720),
  calc_type           calc_type NOT NULL DEFAULT 'scenario_comparison' CHECK (calc_type = 'scenario_comparison'),
  engine_version      varchar(32) NOT NULL CHECK (engine_version ~ '^[0-9]+\.[0-9]+\.[0-9]+$'),
  inputs_hash         char(64) NOT NULL CHECK (inputs_hash ~ '^[0-9a-f]{64}$'),
  result              jsonb NOT NULL CHECK (jsonb_typeof(result) = 'object'),
  created_at          timestamptz NOT NULL DEFAULT now(),
  updated_at          timestamptz NOT NULL DEFAULT now(),
  UNIQUE (id, user_id),
  UNIQUE (id, user_id, currency, snapshot_id, horizon_months),
  UNIQUE (user_id, inputs_hash, engine_version),
  FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
  FOREIGN KEY (currency) REFERENCES currencies(code),
  FOREIGN KEY (snapshot_id, user_id, currency) REFERENCES financial_snapshots (id, user_id, currency) DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX ix_comparisons_user_created ON scenario_comparisons (user_id, created_at DESC);
CREATE INDEX ix_comparisons_snapshot     ON scenario_comparisons (snapshot_id, user_id);

CREATE TABLE scenario_comparison_items (
  comparison_id   uuid NOT NULL,
  position        smallint NOT NULL CHECK (position BETWEEN 1 AND 3),     -- DB-enforced max of 3
  user_id         uuid NOT NULL,
  currency        char(3) NOT NULL,
  snapshot_id     uuid NOT NULL,
  horizon_months  integer NOT NULL,
  scenario_run_id uuid NOT NULL,
  label           varchar(60) NOT NULL,
  PRIMARY KEY (comparison_id, position),
  UNIQUE (comparison_id, scenario_run_id),
  FOREIGN KEY (comparison_id, user_id, currency, snapshot_id, horizon_months)
    REFERENCES scenario_comparisons (id, user_id, currency, snapshot_id, horizon_months) ON DELETE CASCADE,
  FOREIGN KEY (scenario_run_id, user_id, currency, snapshot_id, horizon_months)
    REFERENCES scenario_runs (id, user_id, currency, snapshot_id, horizon_months) DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX ix_comp_items_run ON scenario_comparison_items (scenario_run_id);

-- ---------------------------------------------------------------------
-- 11. AI ANALYSES (context metadata + validated output; no raw prompts, no PII)
-- ---------------------------------------------------------------------
CREATE TABLE ai_analyses (
  id                          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id                     uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  task                        ai_task NOT NULL,
  scenario_run_id             uuid,
  comparison_id               uuid,
  investment_simulation_run_id uuid,
  period_start                date,
  period_end                  date,
  context_schema_version      smallint NOT NULL CHECK (context_schema_version > 0),
  context                     jsonb NOT NULL CHECK (jsonb_typeof(context) = 'object'),   -- sanitized AIContext
  context_hash                char(64) NOT NULL CHECK (context_hash ~ '^[0-9a-f]{64}$'),
  prompt_version              varchar(32) NOT NULL,
  provider                    varchar(40) NOT NULL,
  model                       varchar(100) NOT NULL,
  user_question               varchar(500),
  output                      jsonb NOT NULL CHECK (jsonb_typeof(output) = 'object'),
  validation_status           ai_validation_status NOT NULL,
  validation_details          jsonb NOT NULL DEFAULT '{}',
  input_tokens                integer CHECK (input_tokens >= 0),
  output_tokens               integer CHECK (output_tokens >= 0),
  latency_ms                  integer CHECK (latency_ms >= 0),
  created_at                  timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (scenario_run_id, user_id) REFERENCES scenario_runs (id, user_id) ON DELETE CASCADE,
  FOREIGN KEY (comparison_id, user_id) REFERENCES scenario_comparisons (id, user_id) ON DELETE CASCADE,
  FOREIGN KEY (investment_simulation_run_id, user_id) REFERENCES investment_simulation_runs (id, user_id) ON DELETE CASCADE,
  CHECK (num_nonnulls(scenario_run_id, comparison_id, investment_simulation_run_id) <= 1),
  CHECK (task <> 'explain_scenario'   OR scenario_run_id IS NOT NULL),
  CHECK (task <> 'compare_scenarios'  OR comparison_id IS NOT NULL),
  CHECK (task <> 'explain_investment' OR investment_simulation_run_id IS NOT NULL),
  CHECK (period_end IS NULL OR period_start IS NOT NULL AND period_end >= period_start)
);
CREATE INDEX ix_ai_user_created ON ai_analyses (user_id, created_at DESC);
CREATE INDEX ix_ai_cache_lookup ON ai_analyses (user_id, task, context_hash, prompt_version, model);
CREATE INDEX ix_ai_run          ON ai_analyses (scenario_run_id, user_id) WHERE scenario_run_id IS NOT NULL;
CREATE INDEX ix_ai_comparison   ON ai_analyses (comparison_id, user_id) WHERE comparison_id IS NOT NULL;
CREATE INDEX ix_ai_invrun       ON ai_analyses (investment_simulation_run_id, user_id) WHERE investment_simulation_run_id IS NOT NULL;

-- ---------------------------------------------------------------------
-- 12. NOTIFICATIONS
-- ---------------------------------------------------------------------
CREATE TABLE notifications (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id       uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  type          notification_type NOT NULL,
  severity      notification_severity NOT NULL DEFAULT 'info',
  title         varchar(150) NOT NULL,
  body          varchar(1000) NOT NULL,
  payload       jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(payload) = 'object'),
  due_at        timestamptz NOT NULL DEFAULT now(),
  read_at       timestamptz,
  dismissed_at  timestamptz,
  dedupe_key    varchar(200) NOT NULL,                    -- makes jobs idempotent
  goal_id       uuid,
  loan_id       uuid,
  budget_id     uuid,
  created_at    timestamptz NOT NULL DEFAULT now(),
  updated_at    timestamptz NOT NULL DEFAULT now(),
  UNIQUE (id, user_id),
  UNIQUE (user_id, dedupe_key),
  FOREIGN KEY (goal_id,   user_id) REFERENCES goals   (id, user_id) ON DELETE SET NULL (goal_id),
  FOREIGN KEY (loan_id,   user_id) REFERENCES loans   (id, user_id) ON DELETE SET NULL (loan_id),
  FOREIGN KEY (budget_id, user_id) REFERENCES budgets (id, user_id) ON DELETE SET NULL (budget_id)
);
CREATE INDEX ix_notif_unread ON notifications (user_id, due_at DESC) WHERE read_at IS NULL AND dismissed_at IS NULL;
CREATE INDEX ix_notif_user_created ON notifications (user_id, created_at DESC);
CREATE INDEX ix_notif_goal   ON notifications (goal_id, user_id)   WHERE goal_id IS NOT NULL;
CREATE INDEX ix_notif_loan   ON notifications (loan_id, user_id)   WHERE loan_id IS NOT NULL;
CREATE INDEX ix_notif_budget ON notifications (budget_id, user_id) WHERE budget_id IS NOT NULL;

CREATE TABLE notification_deliveries (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  notification_id uuid NOT NULL,
  user_id         uuid NOT NULL,
  channel         delivery_channel NOT NULL,
  status          delivery_status NOT NULL DEFAULT 'pending',
  attempts        smallint NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  last_error      varchar(255),
  scheduled_at    timestamptz NOT NULL DEFAULT now(),
  sent_at         timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now(),
  UNIQUE (notification_id, channel),
  FOREIGN KEY (notification_id, user_id) REFERENCES notifications (id, user_id) ON DELETE CASCADE,
  CHECK ((status = 'sent') = (sent_at IS NOT NULL))
);
CREATE INDEX ix_deliveries_pending ON notification_deliveries (scheduled_at) WHERE status = 'pending';
CREATE INDEX ix_deliveries_user    ON notification_deliveries (user_id);

CREATE TABLE notification_preferences (
  user_id           uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  notification_type notification_type NOT NULL,
  in_app            boolean NOT NULL DEFAULT true,
  email             boolean NOT NULL DEFAULT false,
  lead_days         smallint NOT NULL DEFAULT 3 CHECK (lead_days BETWEEN 0 AND 30),
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, notification_type)
);

-- ---------------------------------------------------------------------
-- 13. AUDIT LOG (append-only by privilege: app role gets INSERT + SELECT only)
-- ---------------------------------------------------------------------
CREATE TABLE audit_log (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  user_id     uuid REFERENCES users(id) ON DELETE SET NULL,   -- survives account deletion, anonymised
  action      varchar(80) NOT NULL,
  entity_type varchar(50),
  entity_id   uuid,
  outcome     audit_outcome NOT NULL,
  request_id  varchar(64),
  ip_address  inet,
  metadata    jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(metadata) = 'object'),
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_audit_user_created ON audit_log (user_id, created_at DESC);
CREATE INDEX ix_audit_entity       ON audit_log (entity_type, entity_id);
CREATE INDEX ix_audit_action       ON audit_log (action, created_at DESC);

-- ---------------------------------------------------------------------
-- 14. VIEW: goal current amount from the contribution ledger (a sum, not a formula)
-- ---------------------------------------------------------------------
CREATE VIEW v_goal_current_amounts WITH (security_invoker = true) AS
SELECT g.id AS goal_id, g.user_id, g.currency, g.target_amount,
       g.starting_amount + COALESCE(SUM(CASE c.kind WHEN 'contribution' THEN c.amount ELSE -c.amount END), 0)
         AS current_amount
FROM goals g
LEFT JOIN goal_contributions c ON c.goal_id = g.id AND c.user_id = g.user_id
GROUP BY g.id;

-- ---------------------------------------------------------------------
-- 15. TRIGGERS
-- ---------------------------------------------------------------------
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['users','user_profiles','user_preferences','categories','recurring_rules',
    'transactions','budgets','goals','loans','loan_payments','investment_simulations','scenarios',
    'scenario_comparisons','notifications','notification_deliveries','notification_preferences'] LOOP
    EXECUTE format('CREATE TRIGGER trg_%s_updated_at BEFORE UPDATE ON %I
                    FOR EACH ROW EXECUTE FUNCTION set_updated_at()', t, t);
  END LOOP;
END $$;

-- Immutability: result tables cannot be edited after creation (DELETE remains possible).
CREATE TRIGGER trg_immutable BEFORE UPDATE ON financial_snapshots         FOR EACH ROW EXECUTE FUNCTION enforce_immutable();
CREATE TRIGGER trg_immutable BEFORE UPDATE ON financial_projections       FOR EACH ROW EXECUTE FUNCTION enforce_immutable();
CREATE TRIGGER trg_immutable BEFORE UPDATE ON financial_projection_points FOR EACH ROW EXECUTE FUNCTION enforce_immutable();
CREATE TRIGGER trg_immutable BEFORE UPDATE ON scenario_runs               FOR EACH ROW EXECUTE FUNCTION enforce_immutable();
CREATE TRIGGER trg_immutable BEFORE UPDATE ON scenario_comparison_items   FOR EACH ROW EXECUTE FUNCTION enforce_immutable();
CREATE TRIGGER trg_immutable BEFORE UPDATE ON investment_simulation_runs  FOR EACH ROW EXECUTE FUNCTION enforce_immutable();
CREATE TRIGGER trg_immutable BEFORE UPDATE ON goal_progress_snapshots     FOR EACH ROW EXECUTE FUNCTION enforce_immutable();
CREATE TRIGGER trg_immutable BEFORE UPDATE ON loan_schedule_items         FOR EACH ROW EXECUTE FUNCTION enforce_immutable();
CREATE TRIGGER trg_immutable BEFORE UPDATE ON ai_analyses                 FOR EACH ROW EXECUTE FUNCTION enforce_immutable();
CREATE TRIGGER trg_immutable BEFORE UPDATE ON loan_schedules              FOR EACH ROW EXECUTE FUNCTION enforce_immutable('is_current');
CREATE TRIGGER trg_immutable BEFORE UPDATE ON scenario_comparisons         FOR EACH ROW EXECUTE FUNCTION enforce_immutable('name','updated_at');

-- A comparison must have 2 to 3 items at COMMIT (the upper bound is also a CHECK on position).
CREATE FUNCTION check_comparison_item_count() RETURNS trigger LANGUAGE plpgsql AS $$
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
END $$;
CREATE CONSTRAINT TRIGGER trg_comparison_items_ins AFTER INSERT ON scenario_comparisons
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_comparison_item_count();
CREATE CONSTRAINT TRIGGER trg_comparison_items_del AFTER DELETE ON scenario_comparison_items
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION check_comparison_item_count();

-- Minor-unit precision for USER-ENTERED amounts only.
CREATE TRIGGER trg_minor_units BEFORE INSERT OR UPDATE ON transactions            FOR EACH ROW EXECUTE FUNCTION enforce_minor_units('amount');
CREATE TRIGGER trg_minor_units BEFORE INSERT OR UPDATE ON recurring_rules         FOR EACH ROW EXECUTE FUNCTION enforce_minor_units('amount');
CREATE TRIGGER trg_minor_units BEFORE INSERT OR UPDATE ON budgets                 FOR EACH ROW EXECUTE FUNCTION enforce_minor_units('limit_amount');
CREATE TRIGGER trg_minor_units BEFORE INSERT OR UPDATE ON goals                   FOR EACH ROW EXECUTE FUNCTION enforce_minor_units('target_amount','starting_amount','planned_monthly_contribution');
CREATE TRIGGER trg_minor_units BEFORE INSERT OR UPDATE ON goal_contributions      FOR EACH ROW EXECUTE FUNCTION enforce_minor_units('amount');
CREATE TRIGGER trg_minor_units BEFORE INSERT OR UPDATE ON loans                   FOR EACH ROW EXECUTE FUNCTION enforce_minor_units('principal');
CREATE TRIGGER trg_minor_units BEFORE INSERT OR UPDATE ON loan_payments           FOR EACH ROW EXECUTE FUNCTION enforce_minor_units('amount');
CREATE TRIGGER trg_minor_units BEFORE INSERT OR UPDATE ON investment_simulations  FOR EACH ROW EXECUTE FUNCTION enforce_minor_units('initial_amount','periodic_contribution');

COMMIT;

-- =====================================================================
-- OPTIONAL (Phase 10): Row-Level Security as defense in depth.
-- The API sets  SET LOCAL app.current_user_id = '<uuid>'  per request/transaction.
-- Run as the migration/owner role; the runtime role must NOT own tables or have BYPASSRLS.
-- users / refresh_tokens / auth_tokens are excluded: they are looked up before a user is known
-- (by email or token hash) and are protected by hashing and service-layer rules.
-- A separate worker role with BYPASSRLS runs scheduled jobs (recurring txns, reminders).
-- (The view v_goal_current_amounts is security_invoker, so it inherits the caller's RLS on goals.)
-- =====================================================================
-- DO $$
-- DECLARE r record;
-- BEGIN
--   FOR r IN SELECT c.table_name FROM information_schema.columns c
--            JOIN information_schema.tables t USING (table_schema, table_name)
--            WHERE c.table_schema='public' AND t.table_type='BASE TABLE' AND c.column_name='user_id'
--              AND c.table_name NOT IN ('audit_log','refresh_tokens','auth_tokens') LOOP
--     EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', r.table_name);
--     EXECUTE format($p$CREATE POLICY owner_isolation ON %I
--       USING (user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid)
--       WITH CHECK (user_id = NULLIF(current_setting('app.current_user_id', true), '')::uuid)$p$, r.table_name);
--   END LOOP;
-- END $$;
