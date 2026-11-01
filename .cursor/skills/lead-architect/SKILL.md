---
name: lead-architect
description: Reads an Oracle schema into the relationalModel of a Migration Spec. Uses Oracle skills only for that read. Attach this skill only to the Lead Architect bot. Use when that bot is @mentioned in the Migration review group with an Oracle schema.
disable-model-invocation: true
---

# Lead Architect

Enable this skill on the Lead Architect bot only. It runs when that bot is @mentioned in the Migration review group with an Oracle schema. Reply in the group. Do not message the user or the other bots privately. Do not create a routine.

This bot reads the Oracle schema and stops. It does not design the MongoDB schema. Oracle skills are for this read only. Do not enable a MongoDB skill here.

## Official Oracle skills

Also enable the `db` domain from [oracle/skills](https://github.com/oracle/skills) (`npx skills add oracle/skills/db`). Read only:

- [db/agent/schema-discovery.md](https://github.com/oracle/skills/blob/main/db/agent/schema-discovery.md) for columns, nullability, primary keys, foreign keys, unique and check constraints, and row-count estimates.
- [db/sqlcl/sqlcl-ddl-generation.md](https://github.com/oracle/skills/blob/main/db/sqlcl/sqlcl-ddl-generation.md) for the shape of generated Oracle `CREATE TABLE` statements.

The input in this stage is a DDL file. Do not connect to a database, and do not run `ALL_*` queries or SQLcl. Treat the file as the discovery result.

Keep names as written in the file. Do not apply `UPPER()`. Ignore `STORAGE`, segment attributes, `TABLESPACE`, `PCTFREE`, and `ENABLE` on constraints. Keep `NUMBER(p,s)`, `VARCHAR2(n)`, `CHAR(n)`, `CLOB`, and `TIMESTAMP` as the `dataType` string. A missing `NOT NULL` means `nullable: true`. Record `PRIMARY KEY`, `UNIQUE`, `FOREIGN KEY`, and `CHECK` even when the constraint name looks system-generated. A `CLOB` stays a column, and a comment that calls it free text goes in `notes`. A table with no primary key gets `primaryKey: []`.

Do not follow `db/migrations/`. Those guides move other databases into Oracle. Do not write an effort estimate or a cutover plan.

## Inputs

The demo schema is [source.sql](../../../fixtures/order-management/source.sql). The access paths that travel with it are [queries.md](../../../fixtures/order-management/queries.md). The list is markdown: a heading `## <id>`, the statement paragraph, then a line `Needs:` followed by the data elements.

Refuse an ERD or any other image. Ask for the Oracle schema as DDL, plus a written access-path list.

Field names are in [docs/spec-format.md](../../../docs/spec-format.md). Read the Relational model section before writing.

## Sequence

1. Set `sourceSystem` to `oracle` when the DDL uses `VARCHAR2`, `NUMBER`, or `CLOB`. Otherwise use the engine the DDL names. Set `sourceFile` to the repo-relative path of the DDL file.
2. Emit one table per `CREATE TABLE`, in file order. Copy each column in DDL order. `nullable` is `false` when the column is `NOT NULL`.
3. `primaryKey` comes from the primary-key constraint. `uniqueKeys` comes from each `UNIQUE` constraint. `foreignKeys` comes from each `FOREIGN KEY` constraint.
4. Copy a `CHECK` constraint and any comment on that column into `columns[].notes`.
5. Copy the comment block immediately above a `CREATE TABLE` into `cardinalityNote`.
6. Copy each access path verbatim into `accessPaths`: `id` from the heading, `statement` from the paragraph, `needs` from the `Needs` line split on commas. Leave the array empty when no list was supplied. Do not invent a path.

## Return

Post one JSON object in the group. It contains `specVersion` (`"1"`) and `relationalModel` only. Then @Schema Optimization Specialist and stop.

## Validate before posting

- Table count equals `CREATE TABLE` count.
- Every column appears once, in DDL order.
- Every foreign key names a table that exists in this model.
- Access-path statements match the source text.
- The message does not contain `documentModel`, `indexPlan`, `fieldMapping`, or `approval`.

## Approval

This bot does not approve the spec and does not @Cursor Agent Handoff.
