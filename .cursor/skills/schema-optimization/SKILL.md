---
name: schema-optimization
description: Converts the Lead Architect's reading of an Oracle schema into the MongoDB documentModel and fieldMapping, using mongodb-schema-design. Attach this skill only to the Schema Optimization Specialist bot. Use when that bot is @mentioned in the Migration review group after relationalModel is posted.
disable-model-invocation: true
---

# Schema Optimization Specialist

Enable this skill on the Schema Optimization Specialist bot only. It runs when that bot is @mentioned in the Migration review group and the Lead Architect has posted `relationalModel`. Reply in the group. Do not message the user or the other bots privately. Do not create a routine.

Convert that reading into the equivalent MongoDB document model. Do not use [oracle/skills](https://github.com/oracle/skills). The Oracle schema has already been read.

Write `documentModel` and `fieldMapping` only. Leave `relationalModel` unchanged. Field names are in [docs/spec-format.md](../../../docs/spec-format.md).

## Official MongoDB skill

Also enable [mongodb-schema-design](https://github.com/mongodb/agent-skills/tree/main/skills/mongodb-schema-design) from [mongodb/agent-skills](https://github.com/mongodb/agent-skills) on this bot. Read these references before deciding, and do not paste them into the spec:

- `references/fundamental-embed-vs-reference.md`
- `references/fundamental-document-model.md`
- `references/fundamental-document-size.md`
- `references/fundamental-schema-validation.md`
- `references/pattern-polymorphic.md`
- `references/pattern-extended-reference.md`
- `references/pattern-outlier.md`
- `references/antipattern-unnecessary-collections.md`
- `references/antipattern-excessive-lookups.md`

The access paths already on the spec are the workload. Do not ask for an Atlas tier, and do not call `serverStatus`, query stats, or slow-query logs. A snapshot is the extended-reference pattern. A subtype family that shares a query is the polymorphic pattern. An array that can grow past the stated bound is the outlier pattern.

This skill decides the Migration Spec fields. The official skill does not choose a Grok target shape.

## Sequence

1. Read every `cardinalityNote` and every `accessPaths` entry. Treat `needs` as the data that must come back together.
2. Write one relationship per foreign key, plus one `polymorphic` relationship when a column note or `CHECK` closes a subtype set on a single entity.
3. Apply the decision rules in the Document model section of the spec format.
4. Snapshot a field when an access path needs it and the value lives on another table. Keep that entity's own collection. When the parent row does not store the historical value, record in `risks` that the migrated snapshot is the related row at migration time.
5. When the cardinality note says history is retained, or that there is no declared upper bound, use `reference`. A small typical count does not override that.
6. When no access path needs the related document's body, store the foreign key and use `reference`.
7. Use `polymorphic` when those subtypes share an access path that filters the discriminator. One collection, one discriminator, subtype fields required only in the matching `oneOf` branch.
8. When an array has a stated maximum under 100, embed it, set `maxItems` to 100, and record the outlier risk. When the maximum is over 100, or no maximum is stated, use `reference` for that array.
9. Map every source column at least once. Add a second mapping when the same column also feeds a snapshot or a Grok object.
10. Use camelCase paths. The source primary key is `_id`.

## Types and unstructured fields

For an Oracle source, `NUMBER` maps to BSON `long`, `TIMESTAMP` maps to `date`, and `VARCHAR2`, `CHAR`, and `CLOB` map to `string`. A `NUMBER(1)` flag constrained to 0 and 1 uses `valueConversion: "zero-one-to-boolean"` and BSON `bool`. Omit a nullable source value. Write a subtype field only when the row's discriminator owns that field.

A column described as free text with no source schema gets two mappings: `copy` to a `*Raw` path, and `grok` to a sibling object. For `orders.notes`, the Grok job is fixed:

- `invokeWhen`: the source value is a string and contains a non-whitespace character.
- `targetShape`: JSON Schema draft 2020-12, `additionalProperties: false`, required `sentiment`, `issueCategory`, and `summary`.
- `sentiment`: `positive`, `neutral`, or `negative`.
- `issueCategory`: `delivery`, `product`, `billing`, or `other`.
- `summary`: string, `maxLength` 280.

Mirror that object in the order collection's validator. For any other free-text column, write a closed shape with `additionalProperties: false` and stop so the architect can confirm it. Grok does not choose the document layout or the target shape.

## Return

Post `documentModel` and `fieldMapping` as JSON in the group. Then @Performance & Indexing and stop.

## Validate before posting

- One relationship exists per foreign key.
- Every source column has at least one mapping.
- The only `transform: "grok"` mapping is a free-text column, and `orders.notes` uses the shape above.
- `relationalModel` is not repeated and not edited.

[fixtures/order-management/expected-spec.md](../../../fixtures/order-management/expected-spec.md) is this procedure applied to that fixture. For a different source, run the procedure again.

## Approval

This bot does not approve the spec and does not @Cursor Agent Handoff.
