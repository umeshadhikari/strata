-- Minimal data-mart schema for local strata development.
-- Mirrors the real payment-domain shape with smaller column sets so the local
-- pipeline exercises the same code paths (watermarks, partitioning, schema
-- evolution) without the full 100-column source.

CREATE SCHEMA IF NOT EXISTS data_mart;

-- ---------------------------------------------------------------------- --
-- Conformed dimensions
-- ---------------------------------------------------------------------- --

CREATE TABLE data_mart.DIM_CURRENCY (
    CURRENCY_ID        BIGINT PRIMARY KEY,
    CURRENCY_CODE      VARCHAR(3) NOT NULL,
    CURRENCY_NAME      VARCHAR(50) NOT NULL,
    DECIMAL_PLACES     INT NOT NULL DEFAULT 2,
    LAST_UPDATED_TIME  TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE data_mart.DIM_DATA_OWNER (
    DATA_OWNER_ID      BIGINT PRIMARY KEY,
    DATA_OWNER_NAME    VARCHAR(100) NOT NULL,
    DATA_OWNER_TYPE    VARCHAR(20) NOT NULL,
    LAST_UPDATED_TIME  TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE data_mart.DIM_ACCOUNT (
    ACCOUNT_ID         BIGINT PRIMARY KEY,
    ACCOUNT_NUMBER     VARCHAR(34) NOT NULL,
    ACCOUNT_NAME       VARCHAR(200),
    IS_IBAN_ACCOUNT    SMALLINT NOT NULL DEFAULT 0,
    DATA_OWNER_ID      BIGINT REFERENCES data_mart.DIM_DATA_OWNER(DATA_OWNER_ID),
    LAST_UPDATED_TIME  TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE data_mart.DIM_PAYMENT_METHOD (
    PAYMENT_METHOD_ID    BIGINT PRIMARY KEY,
    PAYMENT_METHOD_CODE  VARCHAR(20) NOT NULL,
    PAYMENT_METHOD_NAME  VARCHAR(100) NOT NULL,
    LAST_UPDATED_TIME    TIMESTAMP NOT NULL DEFAULT NOW()
);

-- ---------------------------------------------------------------------- --
-- Facts
-- ---------------------------------------------------------------------- --

CREATE TABLE data_mart.FACT_PAYMENT (
    PAYMENT_ID                  BIGINT PRIMARY KEY,
    DATA_OWNER_ID               BIGINT REFERENCES data_mart.DIM_DATA_OWNER(DATA_OWNER_ID),
    ACCOUNT_ID                  BIGINT REFERENCES data_mart.DIM_ACCOUNT(ACCOUNT_ID),
    CURRENCY_ID                 BIGINT REFERENCES data_mart.DIM_CURRENCY(CURRENCY_ID),
    PAYMENT_METHOD_ID           BIGINT REFERENCES data_mart.DIM_PAYMENT_METHOD(PAYMENT_METHOD_ID),
    AMOUNT                      DECIMAL(18,2) NOT NULL,
    AMOUNT_IN_DEFAULT_CURRENCY  DECIMAL(18,2) NOT NULL,
    VALUE_DATE                  DATE NOT NULL,
    INPUT_TIME                  TIMESTAMP NOT NULL,
    APPROVAL_TIME               TIMESTAMP,
    APPROVER_USER_ID            VARCHAR(50),
    COUNTERPARTY_COUNTRY        CHAR(2),
    STATUS                      VARCHAR(20) NOT NULL,
    LAST_UPDATED_TIME           TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_fact_payment_value_date ON data_mart.FACT_PAYMENT(VALUE_DATE);
CREATE INDEX idx_fact_payment_updated   ON data_mart.FACT_PAYMENT(LAST_UPDATED_TIME);
CREATE INDEX idx_fact_payment_owner     ON data_mart.FACT_PAYMENT(DATA_OWNER_ID);

CREATE TABLE data_mart.FACT_BALANCE (
    BALANCE_ID         BIGINT PRIMARY KEY,
    ACCOUNT_ID         BIGINT REFERENCES data_mart.DIM_ACCOUNT(ACCOUNT_ID),
    CURRENCY_ID        BIGINT REFERENCES data_mart.DIM_CURRENCY(CURRENCY_ID),
    BALANCE_DATE       DATE NOT NULL,
    OPENING_BALANCE    DECIMAL(18,2),
    CLOSING_BALANCE    DECIMAL(18,2) NOT NULL,
    DATA_OWNER_ID      BIGINT REFERENCES data_mart.DIM_DATA_OWNER(DATA_OWNER_ID),
    LAST_UPDATED_TIME  TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_fact_balance_date    ON data_mart.FACT_BALANCE(BALANCE_DATE);
CREATE INDEX idx_fact_balance_updated ON data_mart.FACT_BALANCE(LAST_UPDATED_TIME);

-- ---------------------------------------------------------------------- --
-- Bootstrap reference rows so the dim tables are non-empty
-- ---------------------------------------------------------------------- --

INSERT INTO data_mart.DIM_CURRENCY VALUES
  (1, 'USD', 'US Dollar',    2, NOW()),
  (2, 'EUR', 'Euro',          2, NOW()),
  (3, 'GBP', 'British Pound', 2, NOW()),
  (4, 'JPY', 'Japanese Yen',  0, NOW());

INSERT INTO data_mart.DIM_DATA_OWNER VALUES
  (100, 'Acme Treasury',    'enterprise', NOW()),
  (101, 'Globex Treasury',  'enterprise', NOW()),
  (102, 'Initech Treasury', 'enterprise', NOW());

INSERT INTO data_mart.DIM_PAYMENT_METHOD VALUES
  (1, 'WIRE',  'Wire Transfer',           NOW()),
  (2, 'ACH',   'Automated Clearing House', NOW()),
  (3, 'SEPA',  'SEPA Credit Transfer',    NOW()),
  (4, 'SWIFT', 'SWIFT MT103',             NOW());

-- A couple of accounts to seed the fact table
INSERT INTO data_mart.DIM_ACCOUNT VALUES
  (10001, '0123456789',    'Acme Operating',    0, 100, NOW()),
  (10002, 'GB29NWBK60161331926819', 'Acme UK',  1, 100, NOW()),
  (10003, '9876543210',    'Globex Operating',  0, 101, NOW());

-- Note: facts are populated by local/postgres/seed.py
