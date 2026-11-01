---
name: performance-indexing
description: Adds the MongoDB indexPlan for the document model produced from an Oracle schema, using mongodb-query-optimizer. Attach this skill only to the Performance & Indexing bot. Use when that bot is @mentioned in the Migration review group after documentModel and fieldMapping are posted.
disable-model-invocation: true
---

# Performance and Indexing

Enable this skill on the Performance & Indexing bot only. It runs when that bot is @mentioned in the Migration review group and the MongoDB `documentModel` and `fieldMapping` are already in the chat. Reply in the group. Do not message the user or the other bots privately. Do not create a routine.

Index that MongoDB schema. Do not use [oracle/skills](https://github.com/oracle/skills). Do not edit `documentModel` or `fieldMapping`. If an access path needs a document path that the mapping does not contain, name the gap in `indexPlan.notes` and leave that index out.

The field list is in [docs/spec-format.md](../../../docs/spec-format.md).

## Official MongoDB skill

Also enable [mongodb-query-optimizer](https://github.com/mongodb/agent-skills/tree/main/skills/mongodb-query-optimizer) from [mongodb/agent-skills](https://github.com/mongodb/agent-skills) on this bot. Before adding an index, read:

- `skills/mongodb-query-optimizer/references/core-indexing-principles.md`
- `skills/mongodb-query-optimizer/references/antipattern-examples.md`
- `skills/mongodb-schema-design/references/antipattern-unnecessary-indexes.md`

Order index keys equality, then sort, then range, as those principles describe. There is no cluster in this stage. Do not call `collection-indexes`, `explain`, `find`, or `atlas-get-performance-advisor`. Suggest each index from the access-path shape only. Do not create an index through MCP. The sequence below decides which of those suggestions go into `indexPlan`.

## Sequence

1. Set `workloadProvided` from whether `relationalModel.accessPaths` is empty.
2. When it is empty, `indexes` is `[]` and `notes` says no index is proposed until an access path is stated.
3. Otherwise add one index entry per access path:
   - A point read by the document id uses keys `{ "_id": 1 }`, name `_id_`, and `create: false`.
   - A filter on a discriminator plus a range on a second field, sorted by that same field, uses the discriminator first and the range field second, with the stated sort direction.
   - A list of a parent's documents by a related id, sorted by a time field, uses the document path that stores that id (the snapshot path, when the id was snapshotted) and then the time field in the stated direction.
   - An access path that matches none of those shapes is named in `notes` and gets no index entry.
4. Resolve every key through `fieldMapping` so the index uses document paths (`priceCents`, `customer.customerId`), not source column names.
5. Set `evidence` to `workload` when `notes` cite a measured slow-query log. Otherwise use `stated-query`. Both may be created. `hypothesis` is not a legal evidence value.
6. Set `create: true` only for indexes the script must call `createIndex` for. Name them the way MongoDB renders the keys (`field_1_other_-1`).
7. Add a `partialFilter` only when the served access path restricts the index to a subset of documents.

A foreign key, a source `UNIQUE` constraint, or a column name is not an access path. Leave source unique keys out of `indexPlan`. Say so in `notes` when a unique key was deliberately not promoted. The order-management fixture does this for `products.sku`.

## Return

Post `indexPlan` as JSON in the group. Then ask the human to approve or to name the section that should change. Stop. Do not @Cursor Agent Handoff. Do not @ the other bots.

## Validate before posting

- `workloadProvided` is `false` and `indexes` is empty when no access path was supplied.
- Every index with `create: true` names an `accessPaths[].id`.
- No index exists for a foreign key or a unique constraint that no access path uses.
- The message does not start a cloud agent.

The index plan in [fixtures/order-management/expected-spec.md](../../../fixtures/order-management/expected-spec.md) is this procedure applied to that fixture's three access paths. For a different workload, run the procedure again.

## Approval

Approval belongs to the human in the group. This bot records nothing in `approval`.
