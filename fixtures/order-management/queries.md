# Access paths

The entire workload for this fixture. The Lead Architect copies these three paths into `relationalModel.accessPaths`. No slow-query log is provided.

## q-order-by-id

Given an order id, return that order, its lines, and the customer name and email stored with the order.

Needs: order, lines, customer name, customer email.

## q-products-by-type-price

List products of a given type with price below a given amount, sorted by price ascending.

Needs: product type, price, shared product fields.

## q-recent-orders-by-customer

List a customer's orders from most recent to oldest.

Needs: customer id on the order, placed-at timestamp.
