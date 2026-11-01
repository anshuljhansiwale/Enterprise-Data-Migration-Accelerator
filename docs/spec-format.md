# Migration Spec format

Version `1`. This document is the field list a later JSON Schema should copy. Names below are the JSON property names. A conforming spec is one object with the six properties in [Top level](#top-level). The product sections are the five blocks defined in [product-contract.md](product-contract.md): `relationalModel` (access paths included), `documentModel`, `indexPlan`, `fieldMapping`, and `approval`.

Unknown properties at any object level are invalid. Omit a field rather than sending `null`, except where this document says the value is nullable.

## Top level

| Property | Type | Required | Meaning |
| --- | --- | --- | --- |
| `specVersion` | string | yes | Always `"1"`. |
| `relationalModel` | object | yes | [Relational model](#relational-model). |
| `documentModel` | object | yes | [Document model](#document-model). |
| `indexPlan` | object | yes | [Index plan](#index-plan). |
| `fieldMapping` | object | yes | [Field mapping](#field-mapping). |
| `approval` | object | yes | [Approval record](#approval-record). |

## Relational model

Written by the Lead Architect. Immutable once the Schema Specialist starts.

| Property | Type | Required | Meaning |
| --- | --- | --- | --- |
| `sourceSystem` | string | yes | Engine family the DDL came from, such as `oracle` or `postgres`. |
| `sourceFile` | string | yes | Repo-relative path of the DDL file. |
| `tables` | array of [Table](#table) | yes | One entry per `CREATE TABLE` in the source. |
| `accessPaths` | array of [Access path](#access-path) | yes | The query list, copied verbatim. Empty when the architect supplied none. |

### Table

| Property | Type | Required | Meaning |
| --- | --- | --- | --- |
| `name` | string | yes | Table name as in the DDL. |
| `columns` | array of [Column](#column) | yes | One entry per column, DDL order. |
| `primaryKey` | string array | yes | Column names. Empty only when the source declared no primary key. |
| `uniqueKeys` | array of string array | no | Additional unique constraints. |
| `foreignKeys` | array of [Foreign key](#foreign-key) | no | |
| `cardinalityNote` | string | no | Row-count or growth note copied from the source when present. |

### Column

| Property | Type | Required | Meaning |
| --- | --- | --- | --- |
| `name` | string | yes | |
| `dataType` | string | yes | Vendor type string, such as `VARCHAR2(200)` or `CLOB`. |
| `nullable` | boolean | yes | `false` when the column is `NOT NULL`. |
| `notes` | string | no | Check constraints, discriminators, or comments from the DDL. |

### Foreign key

| Property | Type | Required | Meaning |
| --- | --- | --- | --- |
| `columns` | string array | yes | Columns on this table. |
| `referencesTable` | string | yes | |
| `referencesColumns` | string array | yes | Same length as `columns`. |

### Access path

| Property | Type | Required | Meaning |
| --- | --- | --- | --- |
| `id` | string | yes | Stable id used by `indexPlan`. Pattern: lowercase letters, digits, hyphens. |
| `statement` | string | yes | The access path in the architect's words. |
| `needs` | string array | yes | Data elements the path must return together. These drive embed and snapshot decisions. |

## Document model

Written by the Schema Specialist. Immutable once the Index Planner starts.

| Property | Type | Required | Meaning |
| --- | --- | --- | --- |
| `collections` | array of [Collection](#collection) | yes | Target collections. |
| `relationships` | array of [Relationship](#relationship) | yes | One decision for every foreign key, and one decision for every subtype family. |

### Collection

| Property | Type | Required | Meaning |
| --- | --- | --- | --- |
| `name` | string | yes | MongoDB collection name. |
| `purpose` | string | yes | Which source tables this collection serves. |
| `pattern` | string | yes | `entity`, `polymorphic`, or `reference-target`. |
| `discriminator` | [Discriminator](#discriminator) | when `pattern` is `polymorphic` | Required for polymorphic collections. Forbidden otherwise. |
| `example` | object | yes, unless `pattern` is `polymorphic` | One example document. Forbidden on polymorphic collections. Timestamp fields may be shown as ISO-8601 strings; the validator's `bsonType` is the type the script writes. |
| `examples` | array of object | when `pattern` is `polymorphic` | One example per discriminator value. Replaces `example`. |
| `validation` | [Validation intent](#validation-intent) | yes | The `$jsonSchema` the migration must install. |

### Discriminator

| Property | Type | Required | Meaning |
| --- | --- | --- | --- |
| `field` | string | yes | Document path, usually `type`. |
| `values` | string array | yes | Closed set of subtype values. |

### Validation intent

A condensed `$jsonSchema` body (the object that becomes the `validator.$jsonSchema`). It uses `bsonType`, `required`, `enum`, `properties`, `additionalProperties`, `maxItems`, and `oneOf` as MongoDB schema validation does.

| Property | Type | Required | Meaning |
| --- | --- | --- | --- |
| `bsonType` | string | yes | `object` at the document root. |
| `required` | string array | yes | Shared required fields. |
| `additionalProperties` | boolean | yes | `false` unless the collection's pattern needs open fields. Polymorphic collections keep this `false` and list subtype fields in `oneOf`. |
| `properties` | object | yes | Map of field name to a JSON Schema fragment. |
| `oneOf` | array | no | Required on polymorphic collections: one branch per discriminator value. |

### Relationship

| Property | Type | Required | Meaning |
| --- | --- | --- | --- |
| `id` | string | yes | Stable id. |
| `from` | string | yes | Source table or subtype family. |
| `to` | string | yes | The collection or parent the decision attaches to. |
| `decision` | string | yes | `embed`, `reference`, or `polymorphic`. |
| `snapshot` | boolean | no | Legal only when `decision` is `embed`. `true` when the embedded fields are a point-in-time copy and the living entity also has its own collection. |
| `fields` | string array | when `snapshot` is true | Document paths included in the copy. |
| `why` | string | yes | The access path, cardinality, or shared-query fact that forces this decision. |
| `risks` | string array | yes | May be empty. Include unbounded growth, the 16MB ceiling, and cadence differences when they apply. |

Decision rules the Schema Specialist applies:

- `embed` when the child is always read with the parent, the array is bounded (about 100 or fewer), and parent and child are updated together.
- `embed` with `snapshot: true` when a stated access path needs fields from another entity on this document, and those values must remain the values captured with the parent. Also keep the living entity as its own collection.
- `reference` when the child grows without a stated bound, is read on its own, or is written on a different cadence than the parent.
- `polymorphic` when several subtypes are one entity, share a query, and differ by a closed set of fields. The collection uses one discriminator. Subtype fields are required only inside the matching `oneOf` branch.

## Index plan

Written by the Index Planner. Immutable once approval starts.

| Property | Type | Required | Meaning |
| --- | --- | --- | --- |
| `workloadProvided` | boolean | yes | `true` when `relationalModel.accessPaths` is non-empty. |
| `indexes` | array of [Index](#index) | yes | Empty when `workloadProvided` is `false`. |
| `notes` | string | yes | States that no index is proposed when the access-path list is empty. Otherwise summarizes what the script will and will not create. |

The planner invents no index from a table name, a foreign key, or a column name. An access path that is already served by the default `_id` index is recorded with `create: false`. Indexes with `evidence: "hypothesis"` are forbidden in `indexes`. A guess with no access path is left out, and `notes` says it was left out.

### Index

| Property | Type | Required | Meaning |
| --- | --- | --- | --- |
| `collection` | string | yes | A name in `documentModel.collections`. |
| `keys` | object | yes | Field path to `1` or `-1`, in index prefix order. |
| `name` | string | yes | `_id_` for the default primary index. Otherwise the keys rendered as MongoDB does (`field_1_other_-1`). |
| `servesQuery` | string | yes | An `accessPaths[].id`. |
| `evidence` | string | yes | `stated-query` or `workload`. |
| `create` | boolean | yes | `false` for the default `_id` index. `true` when the migration must call `createIndex`. |
| `partialFilter` | object | no | Present only when the served query filters a subset of documents. |

`evidence: "stated-query"` means an architect wrote the access path and no measured slow-query log was supplied. The script still creates indexes marked `create: true`. `workload` means the key was confirmed by a measured log the spec cites in `notes`.

## Field mapping

Written by the Schema Specialist together with the document model. Immutable once the Index Planner starts.

| Property | Type | Required | Meaning |
| --- | --- | --- | --- |
| `mappings` | array of [Mapping](#mapping) | yes | One entry for every source column, plus extra entries when one column feeds two targets (a raw copy and a Grok object). |

### Mapping

| Property | Type | Required | Meaning |
| --- | --- | --- | --- |
| `sourceTable` | string | yes | |
| `sourceColumn` | string | yes | |
| `targetCollection` | string | yes | |
| `targetPath` | string | yes | Dot path. Use `_id` for the document key. For an embedded array element, the path includes the array name (`lines.quantity`). |
| `transform` | string | yes | `copy`, `snapshot`, `parent`, or `grok`. |
| `valueConversion` | string | no | `none` (default) or `zero-one-to-boolean`. |
| `grok` | [Grok job](#grok-job) | when `transform` is `grok` | Forbidden for every other transform. |

Transform meanings:

- `copy` writes the source value at `targetPath`.
- `snapshot` copies a value onto a parent document from this row or a joined row. The relationship with `snapshot: true` names the fields. The mapping records which source column feeds each of those fields.
- `parent` means the column is the containing document's key. The value is not repeated on the embedded element.
- `grok` classifies an unstructured string into `targetPath`.

### Grok job

| Property | Type | Required | Meaning |
| --- | --- | --- | --- |
| `invokeWhen` | string | yes | Predicate under which the batch may call the model. |
| `targetShape` | object | yes | A JSON Schema document (draft 2020-12) with `additionalProperties: false` and a `required` array listing every allowed property. Keywords inside this object (`$schema`, `type`, `properties`, `enum`, `maxLength`) belong to that schema. |

Grok output rule: a model response is valid only when it parses as JSON and validates against `targetShape`. Extra properties, missing required properties, and enum violations are invalid. The batch calls Grok only for mappings whose `transform` is `grok` and whose `invokeWhen` predicate is true. On an invalid response, the batch appends one JSON line to the reject file for that target collection and does not write that source row's target document. Sibling documents from other source rows are unaffected.

Reject-line properties: `sourceTable`, `primaryKey`, `targetCollection`, `targetPath`, `reason`, `rawModelOutput`.

## Approval record

Written by the migration architect. Absent, or `pending`, until the human gate.

| Property | Type | Required | Meaning |
| --- | --- | --- | --- |
| `status` | string | yes | `pending`, `approved`, or `rejected`. |
| `approver` | string or null | yes | Role or name the architect records. `null` while `pending`. |
| `approvedAt` | string or null | yes | ISO-8601 timestamp. `null` unless `status` is `approved`. |
| `changes` | string array | yes | Edits the architect made before the decision. Empty when they accepted the spec unchanged. |
| `specHash` | string or null | yes | Lowercase hex SHA-256. `null` unless `status` is `approved`. |

### Canonical spec hash

`specHash` is SHA-256 over the UTF-8 bytes of the canonical JSON of the object `{ specVersion, relationalModel, documentModel, indexPlan, fieldMapping }`.

Canonical JSON: object keys sorted lexicographically at every depth, no insignificant whitespace, arrays left in their specified order. The `approval` object is excluded.

The cloud agent computes this hash from the spec it was given and stops when the value differs from `approval.specHash`.
