# Recovery reporting event audit

Read-only inspection on 2026-09-08. No recovery was triggered and no messages were sent. Counts below describe event samples, not verified revival incidents.

## Local BB samples

Sample: five most recently updated threads per provider; latest 300 events per thread, queried from local BB SQLite with `mode=ro`. IDs are SHA-256 prefixes; payload text, tool arguments, titles and credentials are omitted. Active logs can change after sampling.

- codex `c7d95eb05c`: 300 events; completed items agentMessage=9, commandExecution=14, reasoning=8.
- codex `325196775c`: 300 events; completed items agentMessage=7, commandExecution=12, fileChange=2, reasoning=10.
- codex `077a1e68c0`: 155 events; completed items agentMessage=2, commandExecution=9, reasoning=4.
- codex `1a2e6a7d5c`: 300 events; completed items agentMessage=5, commandExecution=15, fileChange=1, reasoning=11, webFetch=5.
- codex `b8a776e82b`: 300 events; completed items agentMessage=5, commandExecution=18, reasoning=7.
- claude-code `b55dabe01d`: 104 events; completed items agentMessage=4, commandExecution=11, fileRead=2, reasoning=12.
- claude-code `cfc24586d9`: 300 events; completed items agentMessage=20, commandExecution=39, delegation=3, fileRead=9, reasoning=19.
- claude-code `53e3b13226`: 32 events; completed items agentMessage=4, commandExecution=3, reasoning=1.
- claude-code `ab49ee6de2`: 28 events; completed items agentMessage=2, commandExecution=3, reasoning=1, toolCall=1.
- claude-code `85e24f4e1e`: 215 events; completed items agentMessage=22, commandExecution=13, fileRead=3, reasoning=13, toolCall=4.
- pi `02b1450942`: 286 events; completed items agentMessage=9, commandExecution=21, fileChange=19, reasoning=18, toolCall=27.
- pi `43c3fb7581`: 106 events; completed items agentMessage=5, commandExecution=10, reasoning=5, toolCall=7.
- pi `c7873815fa`: 80 events; completed items agentMessage=5, commandExecution=3, reasoning=5, toolCall=3.
- pi `8dd5b833ee`: 46 events; completed items agentMessage=1, commandExecution=4, reasoning=2, toolCall=6.
- pi `2d88d6e7e0`: 122 events; completed items agentMessage=2, commandExecution=15, fileChange=2, reasoning=8, toolCall=8.
- acp-cursor `49248d81a6`: 300 events; completed items agentMessage=6, commandExecution=3, fileChange=5, reasoning=9, toolCall=11.
- acp-cursor `a1de8fe50a`: 254 events; completed items agentMessage=17, commandExecution=17, fileChange=14, reasoning=27, toolCall=22.
- acp-cursor `f927666c37`: 300 events; completed items agentMessage=1.
- acp-cursor `7c9d740687`: 244 events; completed items agentMessage=14, commandExecution=18, fileChange=3, reasoning=31, toolCall=20.
- acp-cursor `5328c87f8e`: 69 events; completed items agentMessage=5, commandExecution=5, fileChange=1, reasoning=8, toolCall=4.

Total: 20 BB thread samples, 3841 events. Completed item totals: agentMessage=145, commandExecution=233, delegation=3, fileChange=47, fileRead=14, reasoning=199, toolCall=113, webFetch=5.

## Local CLI samples

Eight most recently modified JSONL files per harness were read from the existing Claude projects, Pi agent sessions and Codex sessions directories. These may include subagent/fork sessions and are not independent outage experiments.

- claude `1255259950`: 1013 rows; assistant=183, tool_result=88, tool_use=88.
- claude `e880203c7c`: 507 rows; assistant=100, tool_result=54, tool_use=54.
- claude `b2f1621785`: 126 rows; assistant=29, tool_result=13, tool_use=13.
- claude `07522c4f77`: 52 rows; assistant=7, tool_result=4, tool_use=4.
- claude `64d1774a98`: 31 rows; assistant=10, tool_result=5, tool_use=5.
- claude `4e895ff3c9`: 17 rows; assistant=5, tool_result=2, tool_use=2.
- claude `63d54e7713`: 18 rows; assistant=5, tool_result=2, tool_use=2.
- claude `38c1414f53`: 47 rows; assistant=8, tool_result=3, tool_use=3.
- pi `cd09900014`: 18 rows; assistant=9, assistant_error=6.
- pi `5a232517d6`: 30 rows; assistant=12, assistant_error=4, toolCall=15.
- pi `da11f7c6c0`: 138 rows; assistant=69, assistant_error=4, toolCall=63.
- pi `68d7aa07e5`: 5 rows; assistant=1.
- pi `73a79002c4`: 8 rows; assistant=4, assistant_error=4.
- pi `0701088a0a`: 8 rows; assistant=4, assistant_error=4.
- pi `aa781e6f7b`: 49 rows; assistant=23, toolCall=17.
- pi `136fcf7a84`: 93 rows; assistant=40, toolCall=42.
- codex `9af9362501`: 933 rows; custom_tool_call=103, function_call=1, task_complete=15, task_started=16.
- codex `8075abd71f`: 89 rows; custom_tool_call=9, function_call=2, task_complete=2, task_started=4.
- codex `c7ae46023a`: 55 rows; custom_tool_call=6, task_started=1.
- codex `7061e6a9e7`: 113 rows; custom_tool_call=13, function_call=3, task_complete=2, task_started=4.
- codex `586bf8c396`: 228 rows; custom_tool_call=23, task_complete=2, task_started=3.
- codex `c875974a8a`: 150 rows; custom_tool_call=20, function_call=1, task_complete=2, task_started=4.
- codex `d649265689`: 238 rows; custom_tool_call=23, function_call=4, task_complete=2, task_started=3.
- codex `24befd5d0a`: 14 rows; task_complete=1, task_started=1.

## Archived fixtures

Inspected all 11 `tests/fixtures/bb*.json` files (147 rows). Named thread captures retain actual archived event shapes; daemon-restart and submission/compaction fixtures are regression representations and are not new live measurements.

- `bb_thr_jg3yv6kthc_events.json`: 2 retryable errors, then final error and failed turn.
- `bb_thr_hnu4c2a76j_events.json`: final error and failed turn.
- `bb_thr_fv5bxrynb7_events.json`: 6 retryable errors, 3 final errors, 3 failed and 14 completed turns.
- `bb_thr_gmgh8s7j9w_events.json`: 10 retryable errors before final error and failed turn.
- `bb_thr_b2q9cmz7zb_events.json`: 2 final errors, 2 failed and 7 completed turns.
- `bb_thr_vp7hipzyr6_events.json`: 4 requested/accepted turns; Cursor failure can be an error-only agent message while turn status says completed.
- `bb_thr_rsgzd56nhq_events.json`: 2 requested/accepted turns and 4 completed agent messages.
- `bb_thr_acbjnabb28_events.json`: 1 requested/accepted turn and 2 completed agent messages.
- Submission fixture: requested → rejected → system error; no acceptance.
- Daemon fixture: requested → started → accepted → interrupted completion → system error → daemon interruption.
- Compaction fixture: 4 retryable provider errors then a final error and failed turn.

## Implementation implications

1. Persist the pre-dispatch BB sequence and request identity. Match `client/turn/requested.data.requestId` to `turn/input/accepted.data.clientRequestId`, then use `scope.turnId`. A next-started-turn heuristic can bind unrelated work. Native retry metadata adds `retryOfRequestId` and `retryAttempt`.
2. Count meaningful agent messages and tool activity. Observed completed tool item types are `commandExecution`, `toolCall`, `fileChange`, `fileRead`, `webFetch`, and `delegation`. Reasoning, acceptance, and token accounting alone do not establish recovery.
3. Treat `provider/error.willRetry=true` as transient. Final `turn/completed` status `failed` or `interrupted`, matched rejection, or a replacement request ends that attempt. Preserve the Cursor error-message special case; completed status alone is insufficient.
4. CLI observation needs file identity/offset and user boundaries. Claude `type=user` includes tool results (`message.content[].type=tool_result`) and metadata; these are not new human requests. Real rows carry `sessionId`, `uuid`, `parentUuid`; assistant rows also carry provider `requestId`.
5. Pi assistant `content[].type=toolCall` with `stopReason=toolUse` is progress. `stopReason=error` and `aborted` must never count as success. An error message is not independently proven to be an exhausted retry lifecycle: no automatic error-to-success sequence without a user boundary was found in this small local sample.
6. Codex emits `event_msg/task_started` and `task_complete` with `payload.turn_id`; user input also appears as `response_item` with `payload.type=message` and `role=user`. Tool calls use `function_call` or `custom_tool_call`. Current logs additionally have `ordinal`; forks may repeat session metadata.
7. Remove wall-clock observation expiry. Persist scan cursors and pending attempts across watcher restarts. Retain an attempt when logs are temporarily inaccessible; end it on explicit replacement/final failure, not an elapsed timer.

## Limits

This validates actual schemas and realistic event diversity, not delivery guarantees. No Discord request, credential change, internet cut, provider failure, or production restart was performed. No live late-recovery timing experiment was performed. Tails can omit request starts; production matching must read enough history or persist identities before dispatch. Partial JSONL writes, file replacement, rate limits, restart persistence, terminal failure and subsequent unrelated output require isolated regression tests.
