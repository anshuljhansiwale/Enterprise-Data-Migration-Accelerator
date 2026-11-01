---
name: cursor-agent-handoff
description: After a human approves the MongoDB schema converted from Oracle, starts one Cursor cloud agent to load it. The agent follows mongodb-connection and mongodb-schema-design. Attach this skill only to the Cursor Agent Handoff bot. Use when that bot is @mentioned and the human has said the spec is approved.
disable-model-invocation: true
---

# Cursor Agent Handoff

Enable this skill on the Cursor Agent Handoff bot only. It runs when that bot is @mentioned in the Migration review group and the human's message says the spec is approved. Reply in the group. Do not message the user or the other bots privately. Do not create a routine.

If you are mentioned before the human has approved, say that approval is still pending and stop. Do not treat another bot's finished index plan as approval.

The spec format is [docs/spec-format.md](../../../docs/spec-format.md). The script boundary is [.cursor/rules/migration-spec.mdc](../../rules/migration-spec.mdc).

## Official MongoDB skills

The cloud agent follows [mongodb-connection](https://github.com/mongodb/agent-skills/tree/main/skills/mongodb-connection) and the validation reference in [mongodb-schema-design](https://github.com/mongodb/agent-skills/tree/main/skills/mongodb-schema-design), both from [mongodb/agent-skills](https://github.com/mongodb/agent-skills). Do not use [oracle/skills](https://github.com/oracle/skills).

This load is one batch process into the MongoDB schema already in the spec. Create one client and reuse it. Use the driver defaults. Do not set pool or timeout parameters, and do not ask for deployment context in the group.

Install each validator already written in the spec. Read `skills/mongodb-schema-design/references/fundamental-schema-validation.md` for how `$jsonSchema` is installed. Do not redesign the validator or the document model.

After a successful load, compare each source table's row count with the MongoDB documents written for it. A rejected source row is not a loaded document.

## Sequence

1. Collect `relationalModel`, `documentModel`, `indexPlan`, and `fieldMapping` from the group. If the human listed edits, those edits are `approval.changes`. Otherwise `changes` is empty.
2. Set `approval.status` to `approved`, `approver` to the human's name when the chat shows one, otherwise `migration architect`, and `approvedAt` to the current time in ISO-8601.
3. Compute `approval.specHash` as the spec format defines it: SHA-256 of the canonical JSON of `{ specVersion, relationalModel, documentModel, indexPlan, fieldMapping }`, keys sorted at every depth, arrays left in order, `approval` excluded. If you cannot compute it, tell the cloud agent to compute it and to stop without writing files when it cannot.
4. Start one Cursor cloud agent on this repository. Give it the four sections, the approval record, and the script rules below. Tell it to follow those rules and not to redesign the model.
5. Post the agent link in the group when it starts. Post the result in the group when it finishes.

## Script rules for the cloud agent

Implement the approved spec in Node.js or Python, one of them, and name the choice in the PR.

- Refuse to write a load when `approval.status` is not `approved` or `approval.specHash` is null.
- Recompute the canonical hash and exit before any write when it differs.
- Create one MongoClient for the process and reuse it, with driver defaults.
- Upsert each target document by `_id` with the source primary key.
- Build documents only from `fieldMapping`. Omit nullable nulls. Apply `valueConversion`.
- Write a subtype field only when that row's discriminator owns the field.
- Install each collection's `validation` as a `$jsonSchema` validator.
- Call `createIndex` only for `indexPlan.indexes` entries with `create: true`, using that entry's `name`, `keys`, and `partialFilter`.
- Call Grok only for mappings whose `transform` is `grok` and whose `invokeWhen` is true.
- Validate every model response against that mapping's `targetShape`. On failure, append one JSON line to the reject file (`sourceTable`, `primaryKey`, `targetCollection`, `targetPath`, `reason`, `rawModelOutput`) and do not write that source row.
- Cache a valid Grok result by `specHash`, source primary key, target path, and a hash of the source text.
- Tests use a stub Grok client. Cover the mappings, a rejected Grok payload, a second run that does not duplicate documents, the `create: true` indexes, the hash refusal, and a source-count to document-count comparison that leaves rejected rows unloaded.

## Validate before starting the agent

- The mention is from the human, and the message approves the spec.
- All four spec sections are present.
- The prompt forbids collections, indexes, and Grok calls the spec does not name.
