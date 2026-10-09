# U-642 — Preserve thinking blocks end-to-end in the intelligence harness

**Status:** DESIGN (Phase 1), awaiting Chris's decisions D1–D4 in §7. Nothing built.
**Author:** `/em`, 2026-10-09. Every `file:line` below was read in worktree `unit/u-642` at `8738fa03` on this date
by two read-only mapping passes and then re-checked by a citation pass (§9). API facts cite Anthropic's
[Thinking](https://platform.claude.com/docs/en/build-with-claude/thinking) page as fetched 2026-10-08.
**Origin:** U-641's Pass 1 (Codex P2). Moving the cascade's Haiku rung to `claude-haiku-5-5` turned thinking on by
default; the harness drops thinking blocks, so a tool-loop continuation replays the assistant turn without them.
The API then silently disables thinking for that request: no 400, but reasoning paid for on the first request
of a turn is lost on every continuation. U-641 shipped with thinking **disabled for Haiku 5.x only**
(`intelligence/transport/anthropic.py:91-98`, `:137-141`). This design removes that stopgap.

---

## 1. The three-sentence answer

Give the harness a `Thinking` part (and its `redacted_thinking` twin), have the Anthropic transport capture each
block complete and byte-identical, let the runner append it in arrival order, and have the Anthropic converter
emit it back verbatim while the OpenAI converter drops it. No schema change: the API requires thinking blocks only
*within* a tool-use turn, which lives in the runner's in-memory history, and prior turns' blocks may be omitted.
Thinking never becomes a `TextDelta` or a loop event, so nothing leaks into persisted text, delegation results,
structured-task parsing, or the web and iOS streams.

---

## 2. Measured ground truth — where thinking is lost today

| Layer | Fact | Where |
|---|---|---|
| Transport events | `TransportEvent = Union[TurnStart, TextDelta, ToolUseStart, ToolUseComplete, TurnEnd, Done, TransportError]`; no thinking event | `intelligence/transport/base.py:63-71` |
| SSE parser | `content_block_start` handles only `tool_use`; `thinking` / `redacted_thinking` starts fall through | `intelligence/transport/anthropic.py:239-252` |
| SSE parser | `content_block_delta` handles only `text_delta` and `input_json_delta`; `thinking_delta` / `signature_delta` dropped | `anthropic.py:253-262` |
| SSE parser | `content_block_stop` pops only tool blocks (`active_tool_blocks`, `:177`) | `anthropic.py:263-276` |
| SSE parser | the dispatch loop is inline inside the httpx context, not a pure function | `anthropic.py:224-297` |
| Message model | `ContentBlock = Union[Text, ToolUse, ToolResult, Image, Document]`, discriminated on `type`; no thinking part | `intelligence/messages/types.py:86-89` |
| Converter (Anthropic) | `_block_to_anthropic` raises `ValueError("Unsupported content block")` on any other part | `intelligence/messages/convert.py:85-112` |
| Converter (OpenAI) | assistant branch keeps only `Text` and `ToolUse`, silently drops the rest | `convert.py:181-205`, comment `:196-197` |
| Converter (OpenAI) | an assistant message with no text and no tool calls becomes `content: None` with no `tool_calls` | `convert.py:198-204` |
| Runner | assistant blocks built from `Text` and `ToolUse` only; text flushed at `tool_use_start` and `turn_end` | `intelligence/loop/runner.py:198-217` |
| Runner | the assistant message is appended to in-memory history in arrival order; tool results appended after dispatch | `runner.py:261-262`, `:288` |
| Runner | unknown event types are ignored (if/elif chain, no else) | `runner.py:186-223` |
| Runner | `transport.stream(...)` passes the declared `model`, never `turn_model`, and no `extra_body` | `runner.py:179-185` |
| Cascade | every rung's events buffer and replay unchanged; a completion with no text/tool events is `empty_completion` and falls back | `intelligence/transport/cascade.py:76-81`, `:87-96` |
| Cascade | every `stream()` restarts at rung 0, so turns of one run land on different models and providers | `cascade.py:66`; ladder `intelligence/cascade/core.py:36-41` |
| Structured tasks | `_complete_structured` concatenates `TextDelta` only (`:237-246`), then parses JSON (`:253`) | `core.py:224-257` |
| Persistence | `AgentTurn` has `assistant_text` only; no column for blocks or order | `intelligence/persistence/session_repo.py:52-69`; `intelligence/persistence/sql/dbo.agent_turn.sql:10-31` |
| Persistence | `load_chain_history` rebuilds an assistant turn as `[Text?] + [ToolUse*]`, already losing block order | `intelligence/persistence/history.py:86-104` |
| Session runner | persists from loop events only; never sees the assistant `Message` | `intelligence/loop/session_runner.py:131-181`, `:222-235` |
| Loop events | no thinking event; every loop event reaches SSE clients | `intelligence/loop/events.py:125-135`; `intelligence/api/router.py:54-57`, `:95` |
| Delegation | forwards an allow-list of event types; builds the sub-agent's result from `text_delta` only | `intelligence/composition/delegation.py:35-43`, `:123-125` |
| Foundry | never emits thinking; reads only `delta.content` and `delta.tool_calls` | `intelligence/transport/foundry.py:255-271` |
| Stopgap | `_THINKING_DISABLED_PREFIXES = ("claude-haiku-5",)`, `_thinking_disabled_model`, the body `thinking: disabled`, the xhigh/max→high clamp | `anthropic.py:91-98`, `:137-141` |
| Tests pinning the stopgap | thinking-disabled and no-thinking-replayed assertions; the file imports `_thinking_disabled_model` at `:20`, so deleting the helper breaks the whole module until the file is updated | `tests/test_u641_haiku_55_transport.py:95-103`, `:117-121`, `:130-133`, `:136-154` |

---

## 3. What the API requires (the design is dictated by these)

1. **Within a tool-use turn, thinking blocks must be passed back** complete and unmodified, alongside the
   `tool_use` they preceded. Across turns it is recommended; outside tool use, prior turns' thinking may be
   omitted. (Thinking docs § Preserving thinking blocks.)
2. **Adaptive mode does not 400 on a missing block**: "no assistant turn needs to start with one"; a mid-turn
   config conflict makes the API "silently disable thinking for that request" and it "may strip thinking blocks
   that would create an invalid turn structure". (§ Thinking with tool use.) This is U-641's degradation.
3. **Wire shapes:** `{"type":"thinking","thinking":"<text or empty>","signature":"<opaque>"}` and
   `{"type":"redacted_thinking","data":"<opaque>"}`. With `display: "omitted"`, the default on Haiku 5.5, the
   `thinking` text is empty and only the `signature` carries the reasoning. Filtering on `type == "thinking"`
   alone silently drops `redacted_thinking` and breaks the protocol. (§ Controlling thinking display, § Redacted
   thinking blocks.)
4. **Streaming order:** a `thinking` block opens with `content_block_start`, streams `thinking_delta`s, then a
   `signature_delta` immediately before `content_block_stop`; a `redacted_thinking` block arrives whole in its
   `content_block_start`. So a block is complete only at `content_block_stop`.
5. **Preservation by model:** Haiku 5.5, Sonnet 4.6+, Opus 4.5+ keep all prior turns' blocks in context;
   Haiku 4.5 and earlier keep the last turn only, and the API strips the rest itself. (§ Thinking block
   preservation by model.)
6. **Model switch:** "Keep passing thinking blocks back unchanged when you switch models"; a block readable only by
   its producer and a fixed set of other models is dropped by the API without an error and without billing.
   Opus 5.5 and Sonnet 5.5 read Haiku 5.5's blocks. (§ Preserved thinking; migration guide, Haiku 5.5.)
7. **History-editing check:** a Haiku 5.5 block stays valid only while `system`, `tools`, and every earlier message
   are unchanged; an edited prefix returns a 400 (enforced by default for accounts created on or after
   2026-08-31, opt-in before). The integration must be append-only. (Migration guide, Haiku 5.5 breaking change 5.)
8. **Account binding:** Haiku 5.5 / Sonnet 5.5 blocks work only in the producing account; a foreign block is
   dropped before the model sees it. One account here, so informational.
9. **Billing:** thinking tokens are output tokens and count toward `max_tokens`. Preserved blocks count as input
   on later requests and are cacheable with tool results.
10. **Turning thinking off** on Haiku 5.5: `thinking: {"type":"disabled"}` at effort low/medium/high only; with
    xhigh/max it is a 400. Sonnet 5.5 rejects `disabled` and uses `{"type":"between_tools"}` instead; Opus 5.5 and
    Fable reject both. (Migration guide.)

---

## 4. The design

### 4.1 Message model — two new parts, in `ContentBlock` only
`intelligence/messages/types.py:86-89` gains `Thinking{type:"thinking", thinking: str, signature: str}` and
`RedactedThinking{type:"redacted_thinking", data: str}`, field names identical to the wire (§3.3). They join
`ContentBlock`, not `OutputBlock` (`:66-69`, the tool-result content union). The module docstring (`:6-13`) lists
them. Without this, `Message(role="assistant", content=[...])` at `runner.py:262` fails validation.

### 4.2 Transport event — one event per complete block
`intelligence/transport/base.py:63-71` gains `ThinkingComplete{type:"thinking_complete", block: Thinking |
RedactedThinking}` in the `TransportEvent` union. One event carrying the part itself: the runner appends `ev.block`
without re-mapping, and the two shapes stay distinct for the converter. It is emitted at `content_block_stop`
(§3.4), never as a `TextDelta` (§2, structured tasks and delegation would otherwise ingest it).

### 4.3 Anthropic parser — per-index accumulation, then a pure function
Mirror the tool-JSON buffer (`anthropic.py:177`, `:259-262`): on `content_block_start` of type `thinking`, open
`active_thinking_blocks[idx] = {"thinking": <start text or "">, "signature": <start signature or "">}`; on
`redacted_thinking`, record `{"data": ...}` whole; append `thinking_delta.thinking` and `signature_delta.signature`
as they arrive; on `content_block_stop`, pop and yield `ThinkingComplete`. **Byte-identical: no strip, no
re-encode, no dedupe.** The inline dispatch (`:224-297`) is split into a pure `async def _sse_to_events(events: AsyncIterator[tuple[str, dict]])
-> AsyncIterator[TransportEvent]` so a recorded transcript drives a unit test, the same async shape as Foundry's
`_chunks_to_events` (`foundry.py:223-290`) and as `_parse_sse` (`anthropic.py:303`). The module docstring (`:6-17`) is updated.

### 4.4 Runner — append in arrival order, flush text first
`runner.py:186-223` gains a `thinking_complete` branch that flushes `text_buf` into a `Text` block exactly as
`tool_use_start` does (`:201-207`) and then appends `ev.block` to `assistant_blocks`. Arrival order is the wire
order, so a thinking block stays ahead of the `tool_use` it led to and the converter's list-order preservation
(`convert.py:54-57`) carries it back intact. **No loop event is yielded** (decision D1): thinking stays out of
`events.py`, so SSE (`_event_to_sse`, `router.py:54-57`, emitted for every event at `:95`), channel history, delegation (`delegation.py:35-43`), `AssistantText`
(`session_runner.py:141-142`, `:222-235`) and the structured-task parser (`_complete_structured`, `core.py:224-257`) are untouched by
construction. The errored path (`runner.py:231-233`) already discards partial blocks; thinking included.

### 4.5 Converters
`_block_to_anthropic` (`convert.py:85-112`) emits the two wire shapes verbatim. `to_openai_request`'s assistant
branch (`:181-205`) drops both parts, and the edge at `:198-204` is closed: an assistant message left with no text
and no tool calls after dropping is **skipped**, not emitted as `content: None`.

### 4.6 Request configuration — an explicit hint replaces the Haiku-prefix rule
Delete `_THINKING_DISABLED_PREFIXES`, `_thinking_disabled_model`, the `thinking: disabled` line and the effort
clamp (`anthropic.py:91-98`, `:137-141`). In their place `_build_request_body` (where the clamp already lives, after the
merge) honours one cross-provider hint, `extra_body["thinking"] == "off"`: for a model that accepts `disabled`
(today the Haiku 5.x prefix) it sends `{"type":"disabled"}` and clamps a mapped xhigh/max effort to high (§3.10);
for every other model the hint is dropped, like any unsupported param (`base.py:84-88`). `_gen_params` never
forwards the hint as a body key. Structured tasks set it in `StructuredTask.gen_params`
(`core.py:60-66`, decision D4): single-shot JSON classification gains nothing from thinking and keeps
`Rung.max_tokens = 1024` (`core.py:30`, used only by `_complete_structured` at `:239`; agent loops get the
caller's `max_tokens` via `cascade.py:74`) safe from thinking consuming the cap. Agent loops pass no `extra_body`
(`runner.py:179-185`) and therefore run adaptive thinking at the model's default effort. Sonnet 5.5's
`between_tools` off-switch is the follow-on unit's problem (§6).

### 4.7 Cascade gate — unchanged
`cascade.py:79` keeps counting only text and tool events as content. A completion that is thinking-only is still
useless to the caller and still falls back. `ThinkingComplete` passes through the per-rung buffer like every other
event (`:76-81`, `:103-104`).

### 4.8 Model and provider switches — pass everything back, let the API decide (decision D3)
The cascade restarts at rung 0 on every call (`cascade.py:66`), so the model that produced turn N and the one
continuing it routinely differ. The harness strips nothing: a Foundry consumer drops the parts in its converter
(§4.5); a Claude consumer receives them and the API drops what it cannot read, unbilled (§3.6), or disables
thinking for a request whose config conflicts mid-turn (§3.2). Tracking an origin model per part to strip
client-side would add state the API already handles. Phase 2 proves this live: a Haiku 5.5 tool turn continued on
Sonnet 4.6 must not 400.

### 4.9 Append-only history — already true, now load-bearing
The runner only appends (`runner.py:262`, `:288`); `system` and `tools` are constant for a run
(`runner.py:179-185`); `cache_control` sits on `system` and the last tool only (`convert.py:70-81`) and never
moves; there is no client-side compaction. A chained session's reconstructed history (`history.py:41-122`) begins a
new turn with a new user message, which §3.1 allows. The one hazard §3.7 names, editing earlier turns, has no
code path here; the design adds a comment at the append sites naming the invariant.

### 4.10 Persistence — deferred (decision D2)
Nothing in §3 requires persisting thinking: the tool loop that must carry it is in memory. Cross-session
continuation would need an ordered content-blocks column on `AgentTurn` (plus `vw_AgentTurn`
`dbo.agent_turn.sql:81-101`, a `CompleteAgentTurn` param with `= NULL` default `:136-171`, the model,
`_from_db` and `AgentTurnRepo.complete` `session_repo.py:52-69`, `:316-341`, `:364-397` (`AssistantText` at `:387`),
its caller `session_runner.py:224-234`, a loop event so `session_runner` can see the blocks, and a `history.py`
rework). That same column would also fix `history.py`'s existing text/tool order loss. It is a DBA
unit on its own merits and is not a prerequisite for closing U-641's finding.

### 4.11 Cost and limits
Thinking tokens are already counted (`Usage.output_tokens`, `base.py:14-21`) and priced by U-641's per-turn sum;
no pricing change. Agent loops run `max_tokens_per_turn = 4096` (`runner.py:114`, and the same default at
`session_runner.py:50`, which is the one agents actually hit because `run.py` passes none); agent loops send no
`output_config`, so effort is the API's default (medium on Haiku 5.5), and that cap now covers thinking plus text. All 13 agents run through the cascade with the default
ladder (`provider="cascade"`, no `ladder=` override; `intelligence/agents/base.py:27`), so **every** agent reaches
the Haiku rung once the three Foundry rungs fail structurally; the three `model="claude-haiku-5-5"` pins are
ignored (`cascade.py:55`). Phase 2 verification records thinking tokens per turn on Haiku-rung turns so both
cap defaults can be raised with evidence, not guessed.

---

## 5. Test plan (Phase 2, all mutation-proven per house rule)

1. **Parser capture:** a recorded SSE transcript (thinking block with deltas and signature, a redacted block, a
   text block, a tool_use block) through `_sse_to_events` yields `ThinkingComplete` events whose `thinking`,
   `signature` and `data` are byte-identical to the transcript, in wire order, before the `ToolUseComplete`.
2. **Two-request regression (the U-641 finding):** `ScriptedTransport` (`tests/test_u641_session_cost.py:21-38`, extended to copy `messages` on every call, since the runner mutates the live `history` list at `runner.py:262` and `:288`)
   yields `ThinkingComplete` then `ToolUseComplete` on turn 1; assert turn 2's request body carries the assistant
   turn as `[thinking, tool_use]` with the identical signature, then the tool_result. Inverts
   `tests/test_u641_haiku_55_transport.py:136-154`.
3. **Converter round-trip:** `Thinking` and `RedactedThinking` → exact wire dicts; OpenAI converter drops them and
   skips an emptied assistant message.
4. **Config hint:** `thinking: "off"` → `disabled` + clamp on `claude-haiku-5-5`; dropped on `claude-sonnet-4-6`,
   `claude-sonnet-5-5`; no hint → no `thinking` key on any model. Replaces `:95-103`, `:117-121`, `:130-133`.
5. **Order:** text, then thinking, then tool_use arriving in that order produce blocks in that order with the text
   flushed before the thinking block.
6. **Structured tasks unaffected:** `_complete_structured` with a thinking event in the stream parses the JSON text
   only.
7. **Live proof at verify (fractions of a cent):** Haiku 5.5 two-request tool loop with thinking on, signature
   round-trips without error; the same turn continued on Sonnet 4.6, no 400; usage shows thinking billed as
   output tokens.

---

## 6. Phase 2 units (each its own row, branch, worktree; §4 approved first)

| Unit | Scope | Tier |
|---|---|---|
| U-642a — capture and replay | §4.1–4.9, §5.1–5.7, delete the Haiku stopgap, update U-641 tests, docstrings | non-P0; Codex `high`; shared primitive ⇒ Pass 2 reuse lens on both converters |
| U-642b — Sonnet 4.6 → 5.5 | ladder rung (the 10 agent `model="claude-sonnet-4-6"` pins are ignored under the cascade; update for honesty only), pricing entry ($2 / $10), `between_tools` as the structured off-switch for Sonnet 5.5, forced `tool_choice` audit (5.5 rejects `any`/`tool`) | non-P0; depends on U-642a |
| U-642c — persist ordered content blocks (optional) | §4.10 | non-P0 but DBA + migration; book only if cross-session fidelity is wanted |

---

## 7. Decisions for Chris

| # | Decision | Recommendation |
|---|---|---|
| D1 | Surface thinking as a loop event (to SSE, web, iOS, delegation)? | **No.** Keep it inside the runner and the transport. Nothing downstream consumes it, and a new loop event reaches two external clients. |
| D2 | Persist thinking for cross-session continuation now? | **Defer** to U-642c. Not required by the API; a DBA unit with its own migration. |
| D3 | Strip thinking client-side when the next turn runs on a different model or provider? | **No.** Pass back unchanged; the API drops unreadable blocks unbilled and degrades mid-turn conflicts gracefully. Prove live in U-642a. |
| D4 | Keep thinking off for single-shot structured tasks, adaptive for agent loops? | **Yes**, via the explicit `thinking: "off"` hint in `StructuredTask.gen_params`. Cheap, deterministic, and `max_tokens 1024` stays safe. |

Decided by the facts, no choice to make: parts live in `ContentBlock` (§4.1); one complete-block event (§4.2);
byte-identical capture (§4.3); arrival-order append with text flush (§4.4); OpenAI converter drops and skips
(§4.5); the Haiku-prefix rule goes (§4.6); the cascade gate stays (§4.7); the harness stays append-only (§4.9).

---

## 8. Risks

- **Thinking cost on agents.** Any agent whose turn falls through the three Foundry rungs pays for Haiku
  thinking (all 13 share the default ladder). Measured in §4.11 before any effort tuning.
- **`max_tokens` truncation pays twice.** A Haiku turn cut off at `max_tokens` while still thinking yields no
  text and no tool events, so the cascade counts it as `empty_completion` and falls back to Sonnet 4.6
  (`cascade.py:79`, `:87-96`), billing both rungs; if the last rung also returns thinking only, the run ends in a
  `LoopError`. Watch the per-turn thinking token count and raise both cap defaults (§4.11) on evidence.
- **Interleaved thinking changes block layout, not tool chaining.** Progress-update blocks (Opus 5.5 / Sonnet 5.5
  / Fable) arrive as separate `thinking` blocks with their own signatures; the per-index capture handles any
  number of them.
- **A test harness that never replays a thinking block proves nothing.** §5.2 asserts the replayed signature,
  not merely the absence of errors.

---

## 9. Citation check
Every `file:line` above was re-derived against `unit/u-642` at `8738fa03` by a read-only pass before this
document was presented: 82 citations checked, 73 exact, 8 corrected by a few lines, 1 naming a function that
does not exist (`_run_structured` → `_complete_structured`), and one claim the code contradicted ("three
Haiku-rung agents" → all 13 agents reach the Haiku rung through the default ladder). Eight omissions it found are
now in §2, §4.3, §4.6, §4.10, §4.11, §5.2 and §8.
