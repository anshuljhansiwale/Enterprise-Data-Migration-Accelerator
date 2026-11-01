-- Demo input: Oracle schema for the order-management migration.
-- The Lead Architect reads this file and nothing else from Oracle.
-- Names are the canonical lowercase names the Migration Spec uses.
-- Cardinality comments are source input. The Lead Architect copies each
-- into relationalModel.tables[].cardinalityNote.

-- customers: about 500000 rows. Profile fields change rarely.
CREATE TABLE customers (
  customer_id  NUMBER(10)     NOT NULL,
  full_name    VARCHAR2(200)  NOT NULL,
  email        VARCHAR2(320)  NOT NULL,
  created_at   TIMESTAMP      NOT NULL,
  CONSTRAINT pk_customers PRIMARY KEY (customer_id)
);

-- addresses: about 3 per customer on average. History is retained.
-- No declared upper bound. Address-book edits are independent of profile edits.
CREATE TABLE addresses (
  address_id    NUMBER(10)     NOT NULL,
  customer_id   NUMBER(10)     NOT NULL,
  label         VARCHAR2(40)   NOT NULL,
  line1         VARCHAR2(200)  NOT NULL,
  line2         VARCHAR2(200),
  city          VARCHAR2(100)  NOT NULL,
  region        VARCHAR2(100),
  postal_code   VARCHAR2(20)   NOT NULL,
  country_code  CHAR(2)        NOT NULL,
  is_current    NUMBER(1)      DEFAULT 1 NOT NULL,
  CONSTRAINT pk_addresses PRIMARY KEY (address_id),
  CONSTRAINT fk_addresses_customer FOREIGN KEY (customer_id) REFERENCES customers (customer_id),
  CONSTRAINT ck_addresses_is_current CHECK (is_current IN (0, 1))
);

-- products: about 20000 rows. type is a closed subtype discriminator.
CREATE TABLE products (
  product_id       NUMBER(10)     NOT NULL,
  sku              VARCHAR2(40)   NOT NULL,
  type             VARCHAR2(20)   NOT NULL,
  name             VARCHAR2(200)  NOT NULL,
  price_cents      NUMBER(10)     NOT NULL,
  author           VARCHAR2(200),
  isbn             VARCHAR2(20),
  page_count       NUMBER(6),
  brand            VARCHAR2(100),
  warranty_months  NUMBER(4),
  size             VARCHAR2(10),
  color            VARCHAR2(40),
  material         VARCHAR2(80),
  CONSTRAINT pk_products PRIMARY KEY (product_id),
  CONSTRAINT uq_products_sku UNIQUE (sku),
  CONSTRAINT ck_products_type CHECK (type IN ('book', 'electronics', 'clothing'))
);

-- orders: about 2000000 rows.
-- notes is free text from the customer or from support. It has no schema.
CREATE TABLE orders (
  order_id         NUMBER(10)  NOT NULL,
  customer_id      NUMBER(10)  NOT NULL,
  ship_address_id  NUMBER(10)  NOT NULL,
  placed_at        TIMESTAMP   NOT NULL,
  status           VARCHAR2(20) NOT NULL,
  notes            CLOB,
  CONSTRAINT pk_orders PRIMARY KEY (order_id),
  CONSTRAINT fk_orders_customer FOREIGN KEY (customer_id) REFERENCES customers (customer_id),
  CONSTRAINT fk_orders_ship_address FOREIGN KEY (ship_address_id) REFERENCES addresses (address_id)
);

-- order_lines: average 3 per order, observed maximum 40.
CREATE TABLE order_lines (
  order_line_id     NUMBER(10)  NOT NULL,
  order_id          NUMBER(10)  NOT NULL,
  product_id        NUMBER(10)  NOT NULL,
  quantity          NUMBER(6)   NOT NULL,
  unit_price_cents  NUMBER(10)  NOT NULL,
  CONSTRAINT pk_order_lines PRIMARY KEY (order_line_id),
  CONSTRAINT fk_order_lines_order FOREIGN KEY (order_id) REFERENCES orders (order_id),
  CONSTRAINT fk_order_lines_product FOREIGN KEY (product_id) REFERENCES products (product_id)
);
