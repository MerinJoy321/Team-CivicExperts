# Implementation Plan: CivicPilot

## Overview

This plan builds CivicPilot in eight phases: (1) project/config foundation, (2) the custom async Scheduler plus its two mandatory concurrency-proof tests, (3) the tool layer (Search/Fetch/Cache/Model/Document/Portal wrappers), (4) the five CrewAI agent roles wired exclusively through the SchedulerToolProxy, (5) the pipeline logic that gives the agents their reasoning behavior (Intake, FilterPipeline, verification engine, adaptive evidence loop, end-to-end orchestration), (6) the Streaming UI, (7) hardening tests for error/failure/timeout/cache paths, and (8) cross-profile accuracy and real-world latency validation. Implementation language is Python, matching the design document's code samples (CrewAI, asyncio, ChromaDB, python-docx). The Phase 2 concurrency tests (Task 7) are a **blocking gate**: Task 9 and everything after it MUST NOT begin until both tests pass, per the design's testing strategy and Requirement 3.

## Tasks

- [x] 1. Set up project structure and configuration foundation
  - [x] 1.1 Create project structure and package layout
    - Set up the Python package layout (e.g. `civicpilot/{scheduler,tools,agents,pipeline,ui,telemetry,config}`), `pyproject.toml`/`requirements.txt` pinning CrewAI, asyncio-compatible HTTP client, `chromadb`, `python-docx`, `pytest`, `pytest-asyncio`, and `hypothesis`
    - _Requirements: N/A (project scaffolding, prerequisite for all subsequent tasks)_

  - [x] 1.2 Implement environment configuration loader
    - Load `MAX_SEARCH_CONCURRENCY` (default 4), `MAX_FETCH_CONCURRENCY` (default 4), `MAX_VERIFY_CONCURRENCY` (default 3) from `.env`; reject non-integer or out-of-`[1,32]` values by falling back to the default and recording a startup warning naming the invalid variable
    - Load Fast_Model and Reasoning_Model provider config (model identifier, provider endpoint, auth credential) from `.env`; fail startup with a role-identifying error if either role's config is missing or invalid
    - _Requirements: 6.3, 6.4, 6.5, 6.8, 23.1, 23.2, 23.3, 23.4, 23.5, 23.6_

  - [x] 1.3 Implement structured logging
    - Set up a logging module usable by the Scheduler, tools, agents, and pipeline for diagnostic output (distinct from the citizen-facing Streaming_UI trace)
    - _Requirements: N/A (operational support for all subsequent components)_

  - [x] 1.4 Implement timing/instrumentation utilities
    - Implement monotonic-clock helpers (`now_s()`, elapsed measurement) that the Telemetry_Module and Scheduler will use for `started_at`/`completed_at`/batch timing; millisecond-precision formatting helpers for the eventual telemetry snapshot fields
    - _Requirements: 4.6 (supporting utility; full telemetry recording implemented in Task 6)_

  - [x] 1.5 Write unit tests for the config loader
    - Test each of `MAX_SEARCH_CONCURRENCY`, `MAX_FETCH_CONCURRENCY`, `MAX_VERIFY_CONCURRENCY` at boundary values (0, 1, 32, 33, non-integer) and confirm fallback-to-default plus warning
    - Test missing/invalid Fast_Model and Reasoning_Model config causing startup failure with a role-identifying error
    - _Requirements: 6.4, 23.4_

  - [x] 1.6 Write property test for concurrency config fallback
    - **Property 21: Invalid concurrency configuration always falls back to the documented default**
    - **Validates: Requirement 23.4**

- [x] 2. Checkpoint - Ensure foundation tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 3. Implement Scheduler task model and dependency graph
  - [x] 3.1 Define `ToolCategory`, `TaskStatus` enums and the `ToolTask` dataclass
    - Fields exactly as designed: `task_id`, `category`, `tool_name`, `params`, `priority` (1-10), `timeout_ms` (>0), `depends_on`, `agent_role`, `status`, `retries_used`, `result`, `error`, `started_at`, `completed_at`, `dependency_satisfied_at`
    - _Requirements: 2.1_

  - [x] 3.2 Implement dependency graph resolution
    - Transition a task `PENDING -> ELIGIBLE` the instant every entry in `depends_on` reaches `COMPLETED`
    - Transition a task to `SKIPPED` (without ever executing it) if any of its dependencies reaches `FAILED`, `TIMED_OUT`, or `CANCELLED`, propagating transitively to that task's own dependents
    - _Requirements: 2.2, 2.11_

  - [x] 3.3 Write property test for dependency-failure skip propagation
    - **Property 5: Failed, timed-out, or cancelled dependencies always skip (never execute) their dependents**
    - **Validates: Requirement 2.11**

- [x] 4. Implement Scheduler priority queue and concurrency gates
  - [x] 4.1 Implement per-category priority heap with tie-break ordering
    - Binary heap keyed by `(priority, dependency_satisfied_at)` per `ToolCategory`, lower priority number dispatched first, ties broken by earliest dependency-satisfaction timestamp
    - _Requirements: 2.4_

  - [x] 4.2 Implement per-category concurrency gates
    - One `asyncio.Semaphore` per `ToolCategory`, sized from the Task 1.2 config loader (`MAX_SEARCH_CONCURRENCY`, `MAX_FETCH_CONCURRENCY`, `MAX_VERIFY_CONCURRENCY`); a task leaves the queue only when `ELIGIBLE` and a slot is available
    - _Requirements: 2.3, 23.5_

  - [x] 4.3 Implement `Scheduler.submit()` and the main scheduling loop
    - Wire the task model (3.x), priority heap (4.1), and concurrency gates (4.2) into `submit(task) -> Future[ToolTask]` and the `run()` loop described in the design
    - _Requirements: 1.3, 2.1, 2.2, 5.1_

  - [x] 4.4 Write property test for independent-task concurrency
    - **Property 1: Independent tasks never serialize**
    - **Validates: Requirements 1.3, 2.2, 5.1**

  - [x] 4.5 Write property test for the concurrency ceiling
    - **Property 2: Concurrency never exceeds the configured maximum**
    - **Validates: Requirements 2.3, 3.7, 9.3, 11.2, 23.5**

  - [x] 4.6 Write property test for priority/FIFO dispatch ordering
    - **Property 4: Priority and FIFO tie-break ordering is respected**
    - **Validates: Requirement 2.4**

- [x] 5. Implement Scheduler timeout, retry, cancellation, and failure isolation
  - [x] 5.1 Implement the timeout wrapper
    - Wrap each execution in `asyncio.wait_for(..., timeout=task.timeout_ms/1000)`; on `TimeoutError`, set status to `TIMED_OUT` with no retry
    - _Requirements: 2.5, 18.6_

  - [x] 5.2 Implement the retry policy
    - On a tool-reported retryable error (including any `Recoverable_HTTP_Status`), retry exactly once using the same timeout as the original attempt; mark `FAILED` if the retry also fails; non-retryable errors go straight to `FAILED` with no retry
    - _Requirements: 2.6, 18.7, 18.8, 18.9_

  - [x] 5.3 Implement `cancel()` and failure isolation
    - `cancel(task_id)` transitions `PENDING`/`RUNNING` -> `CANCELLED`, cancelling the underlying `asyncio.Task` if running
    - Ensure the scheduling loop never awaits one task before dispatching sibling-eligible tasks, so a failure only affects tasks that declared a dependency on it
    - _Requirements: 2.7, 2.9, 5.2, 19.1, 19.2_

  - [x] 5.4 Write property test for the retry cap
    - **Property 6: Retry count never exceeds one**
    - **Validates: Requirements 2.6, 18.7, 18.8**

  - [x] 5.5 Write property test for sibling failure isolation
    - **Property 7: A sibling's failure never blocks or delays independent tasks**
    - **Validates: Requirements 2.7, 5.2, 8.7, 11.5, 19.1, 19.2**

- [x] 6. Implement Telemetry hooks and Critical_Path_Latency computation
  - [x] 6.1 Implement `TelemetryModule` task/batch lifecycle hooks
    - `on_task_start`, `on_task_complete`, and `on_batch_complete(category, batch_size, elapsed_s)`, firing whenever a category's concurrently-running set drains to zero
    - Track the maximum number of tasks observed with overlapping execution windows at any point in time, per category and overall
    - _Requirements: 2.8, 2.10, 3.7, 4.6, 4.7_

  - [x] 6.2 Implement `compute_critical_path_latency`
    - Build the dependency DAG from tasks' `depends_on` edges using measured `started_at`/`completed_at`, and return the maximum over all root-to-leaf paths of the summed measured durations along that path
    - _Requirements: 3.6, 4.8_

  - [x] 6.3 Wire telemetry hooks into the Scheduler lifecycle
    - Call `on_task_start`/`on_task_complete` from the exact state-transition points implemented in Tasks 3-5; no polling or timer-driven telemetry updates
    - _Requirements: 2.8, 2.10_

  - [x] 6.4 Write property test for critical path computation
    - **Property 3: Critical path latency is bounded by the longest dependency chain, not the sum of all task durations**
    - **Validates: Requirements 3.6, 4.8**

- [x] 7. Implement mandatory concurrency verification tests (Requirement 3) — REQUIRED BLOCKING GATE
  - [x] 7.1 Implement Concurrency Test A: 5-task ~2-3s proof
    - Schedule 5 independent fake tasks (`asyncio.sleep` stand-ins) each with an artificial duration of exactly 2.0s (±0.2s tolerance); assert total elapsed wall-clock time ≤ 3.0s and that the Telemetry_Module reports 5 maximum overlapping tasks
    - _Requirements: 3.1, 3.2, 3.3, 3.7_

  - [x] 7.2 Implement Concurrency Test B: 4 search / 4 fetch / 3 verify dependency chain
    - Schedule 4 fake search tasks, 4 fake fetch tasks (each depending on its corresponding search task), and 3 fake verification tasks (each depending on the fetch tasks it needs), durations between 1.0-2.0s each
    - Assert measured Critical_Path_Latency does not exceed `min(1.2 * theoretical_critical_path, 0.8 * sum_of_all_11_durations)`, and assert the Telemetry_Module reports at least 4 maximum overlapping tasks during the search/fetch stages
    - _Requirements: 3.4, 3.5, 3.6, 3.7_

- [x] 8. Checkpoint — REQUIRED BLOCKING GATE: both concurrency tests (7.1, 7.2) MUST pass before any Phase 3+ task (Task 9 onward) begins
  - Ensure all tests pass, ask the user if questions arise. Do not proceed to Task 9 until Test A and Test B are green.

- [x] 9. Implement tool layer: Search_Tool and Fetch_Tool wrappers
  - [x] 9.1 Implement `SearchToolProxy` (Tavily wrapper)
    - Wraps Tavily; every issued query set includes at least one `site:myscheme.gov.in`-scoped query; submits via `Scheduler(category=SEARCH, timeout=SEARCH_TIMEOUT_S)` with the 6-8s timeout range from Requirement 18.1
    - _Requirements: 9.2, 18.1_

  - [x] 9.2 Implement `FetchToolProxy` (Jina Reader wrapper)
    - Checks Cache_Store for an unexpired URL entry before issuing a fetch; on miss, submits via `Scheduler(category=FETCH, timeout=FETCH_TIMEOUT_S)` with the 8-10s timeout range from Requirement 18.2; classifies recoverable vs. non-recoverable fetch failures for the Scheduler's retry policy
    - _Requirements: 11.1, 11.3, 11.4, 11.5, 12.1, 18.2_

  - [x] 9.3 Write unit tests for Search/Fetch proxy timeout and error handling
    - Test recoverable-error retry-once behavior and non-recoverable immediate-failure behavior for both proxies; test that a fetch failure excludes the source without blocking sibling fetches
    - _Requirements: 11.4, 11.5, 18.1, 18.2_

- [x] 10. Implement tool layer: Cache_Store (ChromaDB)
  - [x] 10.1 Implement `CacheStore.get`/`put`
    - Support `url`, `scheme`, and `profile_category` key kinds per the Chroma schema in the design; `get` returns `None` for absent or >24h-old entries; `put` is a no-op for entries with `status` in `{"partial", "failure"}`; record timestamp, source_id, status, confidence (0.0-1.0), content_payload
    - _Requirements: 12.1, 12.2, 12.3, 12.4, 12.5, 12.6, 12.7_

  - [x] 10.2 Write property test for cache write gating
    - **Property 10: Cache entries with failure or partial status are never persisted as authoritative**
    - **Validates: Requirement 12.5**

  - [x] 10.3 Write property test for cache expiry
    - **Property 11: Expired cache entries are never treated as hits**
    - **Validates: Requirements 12.6, 12.7**

- [x] 11. Implement tool layer: Fast_Model and Reasoning_Model wrappers
  - [x] 11.1 Implement `ModelClient` abstraction and provider config wiring
    - Provider-agnostic client interface reading the Task 1.2-loaded model identifier and connection parameters; role-to-model mapping fixed at startup for the life of the process
    - _Requirements: 6.1, 6.2, 6.6, 6.7, 6.8_

  - [x] 11.2 Implement Fast_Model and Reasoning_Model wrapper calls routed through the Scheduler
    - Fast_Model calls submitted as `ToolTask(category=FAST_MODEL)` with a 2-4s timeout (Requirement 18.4); Reasoning_Model calls submitted as `ToolTask(category=REASONING_MODEL)` with a 15-30s timeout (Requirement 18.5)
    - _Requirements: 18.4, 18.5_

  - [x] 11.3 Write unit tests for model wrapper timeout enforcement
    - Test that Fast_Model and Reasoning_Model calls are bounded by their configured timeout ranges and that a missing/invalid role config fails startup with a role-identifying error
    - _Requirements: 6.4, 18.4, 18.5_

- [x] 12. Implement tool layer: Document_Generator and Portal_Validator
  - [x] 12.1 Implement `DocumentGenerator` (python-docx)
    - Accepts only a single `SchemeCandidate`'s own criteria/info payload (no access to the full candidate list), structurally preventing cross-scheme leakage
    - _Requirements: 15.3_

  - [x] 12.2 Implement `PortalValidator`
    - Deterministic, no I/O, no LLM: confirms official status only for domains ending exactly in `.gov.in`, exactly in `.nic.in`, or an exact match in a `config/official_domains.yaml`-loaded state-domain registry
    - _Requirements: 16.1, 16.2, 22.4_

  - [x] 12.3 Write property test for document scheme isolation
    - **Property 15: Generated documents never contain another scheme's data**
    - **Validates: Requirement 15.3**

  - [x] 12.4 Write property test for portal domain matching
    - **Property 16: Portal validator never labels a non-matching domain as official**
    - **Validates: Requirements 16.1, 16.2**

- [x] 13. Checkpoint - Ensure tool layer tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 14. Implement CrewAI agents: Planner_Agent and Researcher_Agent
  - [x] 14.1 Implement `PlannerAgent.plan`
    - Issues exactly one `REASONING_MODEL` `ToolTask` (30s timeout) returning 3-5 distinct `SearchOperation`s plus a dependency annotation between them; validates count and pairwise distinctness; discards and raises `PlanningFailure` on any violation rather than truncating/padding
    - _Requirements: 8.1, 8.2, 8.3, 8.4_

  - [x] 14.2 Implement `ResearcherAgent` skeleton
    - Submits search/fetch `ToolTask`s via `SearchToolProxy`/`FetchToolProxy` respecting the Planner's dependency annotations (independent ops scheduled concurrently, dependent ops after their prerequisite completes); a failed search does not halt sibling searches
    - Define a `FilterPipeline` interface (protocol/ABC) as a constructor dependency; wire a pass-through stub implementation for now — full logic lands in Task 19
    - _Requirements: 8.5, 8.6, 8.7, 9.1, 9.3, 9.4, 9.5_

  - [x] 14.3 Write property test for search plan shape
    - **Property 17: Search plans are always sized 3-5 and pairwise distinct**
    - **Validates: Requirements 8.1, 8.3**

  - [x] 14.4 Write unit test for exactly-one Reasoning_Model call per plan
    - Mock call-count assertion on `PlannerAgent.plan`
    - _Requirements: 8.2_

- [x] 15. Implement CrewAI agents: Verifier_Agent, Document_Advisor_Agent, Reporter_Agent
  - [x] 15.1 Implement `VerifierAgent` skeleton
    - Submits verification `ToolTask`s via the Scheduler with the configured `MAX_VERIFY_CONCURRENCY` gate for concurrent, non-dependent scheme verifications; define `evaluate_criterion`/`derive_overall_result`/`assess_sufficiency` as interface stubs — full logic lands in Tasks 20-21
    - _Requirements: 14.6_

  - [x] 15.2 Implement `DocumentAdvisorAgent.maybe_generate`
    - Gate: `overall == ELIGIBLE` and `confidence_level == HIGH` and `identity_info_complete` and `application_info_complete`; on pass, submits a fire-and-forget `ToolTask(category=DOCUMENT)` via `DocumentGenerator` so the eligibility result is never delayed; on gate failure or generation error, returns `DocumentOutcome(generated=False, ...)` without invalidating the delivered eligibility result
    - _Requirements: 15.1, 15.2, 15.4, 15.5_

  - [x] 15.3 Implement `ReporterAgent.synthesize`
    - Assembles the user-facing `FinalReport`: per-scheme result, confidence, `PortalValidator`-backed official-portal annotation on every citizen-facing link, and a readable performance trace (tool name, PARALLEL/SEQUENTIAL mode, completed/total count, elapsed seconds per batch); never blocks on `DocumentAdvisorAgent`
    - _Requirements: 4.9, 16.3, 16.4_

  - [x] 15.4 Write property test for the document-generation gate
    - **Property 14: Document generation only occurs for HIGH-confidence ELIGIBLE results with complete info**
    - **Validates: Requirements 15.1, 15.2**

  - [x] 15.5 Write unit test for performance trace text formatting
    - Assert the rendered trace includes tool name, execution mode, completed/total counts, and elapsed seconds per Requirement 4.9
    - _Requirements: 4.9_

- [x] 16. Implement architecture guard enforcement across all agents
  - [x] 16.1 Implement `SchedulerToolProxy` and `_ARCHITECTURE_GUARD`; wire all five agents to use it exclusively
    - The proxy is the sole code path any CrewAI agent (Planner, Researcher, Verifier, Document_Advisor, Reporter) has access to for reaching Tavily/Jina/ChromaDB/LLM/docx clients; any attempt to invoke a tool outside a registered agent context raises `ArchitectureViolationError`, rejects the invocation without executing the tool, and leaves prior completed operations unchanged
    - Attribute every reasoning output (plan, finding, verification result, document recommendation, report content) to the specific agent role that produced it
    - _Requirements: 1.1, 1.2, 1.4, 1.5_

  - [x] 16.2 Write unit test for architecture-violation rejection
    - Assert a direct tool-client call bypassing the proxy raises `ArchitectureViolationError`, is not executed, and does not affect previously completed operations
    - _Requirements: 1.5_

- [x] 17. Checkpoint - Ensure agent layer tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 18. Implement pipeline logic: Intake_Module
  - [x] 18.1 Implement `IntakeModule.extract_profile`
    - Deterministically rejects input that is empty, whitespace-only, or >5000 characters with `IntakeRejectionError` (no LLM call for this check); otherwise issues exactly one `ToolTask(category=FAST_MODEL, tool_name="extract_profile")` with a JSON-schema-constrained prompt; maps parsed JSON onto `Profile` fields, leaving any field not explicitly grounded in the input as `None`; raises `ProfileExtractionError` (never returns a partial `Profile`) if the call fails or the response is not valid, schema-conformant JSON
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6_

  - [x] 18.2 Write unit tests for intake rejection and extraction failure
    - Test empty/whitespace/>5000-char rejection and malformed-JSON/failed-call extraction failure
    - _Requirements: 7.2, 7.6_

- [x] 19. Implement pipeline logic: FilterPipeline
  - [x] 19.1 Implement `FilterPipeline.dedupe`
    - Deterministic URL normalization (strip trailing slash, sort query params, force scheme-insensitive host) dropping exact-normalized duplicates; pure, no I/O
    - _Requirements: 10.1_

  - [x] 19.2 Implement `FilterPipeline.remove_irrelevant`
    - Deterministic term/topic-overlap check of title+snippet against the search query
    - _Requirements: 10.2_

  - [x] 19.3 Implement `FilterPipeline.classify_ambiguous` and `rank_and_select`
    - `classify_ambiguous` issues a `FAST_MODEL` `ToolTask` only for candidates deterministic filtering could not decide, retaining candidates unscored on failure rather than dropping them; `rank_and_select` tier-ranks per the Requirement 9.5 6-tier order and returns the top 5 (or fewer, down to 0, if fewer remain)
    - _Requirements: 9.5, 10.3, 10.4, 10.5_

  - [x] 19.4 Wire the full `FilterPipeline` into `ResearcherAgent`, replacing the Task 14.2 stub
    - `ResearcherAgent.research` now runs search wave -> `FilterPipeline` (dedupe -> remove_irrelevant -> classify_ambiguous -> rank_and_select) -> fetch wave via the concurrency-gated `Scheduler(category=FETCH)`
    - _Requirements: 10.6, 11.2_

  - [x] 19.5 Write property test for URL dedup idempotence
    - **Property 12: URL deduplication is a stable idempotent normalization**
    - **Validates: Requirement 10.1**

  - [x] 19.6 Write property test for filtered selection bounds
    - **Property 13: Filtered candidate selection never exceeds 5 and never fabricates candidates**
    - **Validates: Requirement 10.5**

- [x] 20. Implement pipeline logic: eligibility verification engine
  - [x] 20.1 Implement deterministic criterion comparator resolution
    - If `criterion.comparator`/`threshold` can be checked directly against a non-null `Profile` field, resolve `PASS`/`FAIL` with no model call; if the required field is null/missing, classify `UNKNOWN` immediately and never assume a default or `PASS`
    - _Requirements: 14.1, 14.3_

  - [x] 20.2 Implement Reasoning_Model escalation for non-deterministic criteria
    - Criteria not resolvable by direct comparison escalate to a `REASONING_MODEL` `ToolTask`; any non-definitive result or failed response classifies `UNKNOWN`
    - _Requirements: 14.4, 14.5_

  - [x] 20.3 Implement `derive_overall_result`
    - `NOT_ELIGIBLE` if any criterion is `FAIL` (regardless of `UNKNOWN`s); else `ELIGIBLE` iff every criterion is `PASS`; else `POSSIBLE_NEEDS_INFO`; result is identical regardless of criterion evaluation/listing order
    - _Requirements: 14.2_

  - [x] 20.4 Wire the verification engine into `VerifierAgent`, replacing the Task 15.1 stub
    - `VerifierAgent.evaluate_criterion`/`derive_overall_result` now delegate to Tasks 20.1-20.3; concurrent, independent scheme verifications remain gated by `MAX_VERIFY_CONCURRENCY`
    - _Requirements: 14.6_

  - [x] 20.5 Write property test for UNKNOWN safety
    - **Property 8: UNKNOWN never becomes PASS**
    - **Validates: Requirements 14.3, 14.5, 19.3**

  - [x] 20.6 Write property test for overall-result derivation
    - **Property 9: Overall eligibility derivation is a pure, order-independent function of criterion classifications**
    - **Validates: Requirement 14.2**

- [x] 21. Implement pipeline logic: adaptive evidence-sufficiency loop
  - [x] 21.1 Implement `assess_sufficiency`
    - Evidence is sufficient when every applicable criterion for a determination has resolved evidence and no unresolved conflict would change the outcome; returns which criteria remain unresolved
    - _Requirements: 13.1_

  - [x] 21.2 Implement targeted gap-fill `ToolTask` emission bounded to 5 cycles
    - On insufficiency, emit only the additional searches/fetches/verifications targeting the specific unresolved criteria, and repeat the sufficiency assessment after each additional operation, capped at 5 cycles per determination; on sufficiency, skip remaining planned operations and record which ones were skipped
    - _Requirements: 13.2, 13.3, 13.4_

  - [x] 21.3 Implement unresolved-criteria reporting when cycles are exhausted
    - If evidence remains insufficient after 5 cycles, produce a determination indicating the outcome could not be confidently established and identify the unresolved criteria
    - _Requirements: 13.5_

  - [x] 21.4 Write property test for adaptive-cycle boundedness and monotonic resolution
    - **Property 18: Adaptive evidence cycles are bounded and monotonically resolve or exhaust**
    - **Validates: Requirements 13.3, 13.4, 13.5**

- [x] 22. Implement pipeline logic: failure isolation and confidence degradation
  - [x] 22.1 Implement degraded-result confidence downgrade logic
    - If all tools/sources needed for a scheme's determination fail, classify affected criteria `UNKNOWN` and force overall to `POSSIBLE_NEEDS_INFO` rather than fabricating a result; whenever a result is built from evidence affected by one or more tool/source failures, set `degraded=True` and report a `confidence_level` strictly lower than an equivalent failure-free run, visibly to the citizen
    - _Requirements: 19.3, 19.4_

  - [x] 22.2 Write property test for confidence degradation
    - **Property 19: Confidence is never higher than an equivalent failure-free run**
    - **Validates: Requirement 19.4**

- [x] 23. Implement pipeline logic: end-to-end orchestration wiring
  - [x] 23.1 Implement the top-level orchestrator
    - Wire `IntakeModule -> PlannerAgent -> ResearcherAgent -> (VerifierAgent + assess_sufficiency adaptive loop) -> ReporterAgent`, with `DocumentAdvisorAgent` invoked fire-and-forget after eligibility results are ready, never blocking delivery
    - Apply the same pipeline stage sequence regardless of profile type or scheme, using only profile/scheme data as variation (no identifier-based conditional branches on scheme name, department, document name, URL, state, or user-type)
    - _Requirements: 15.4, 17.1, 17.2, 22.1, 22.2, 22.3, 22.4_

  - [x] 23.2 Wire Telemetry snapshot production and failure classification into the orchestrator
    - Produce a `TelemetrySnapshot` per request (intake/planning/search/filtering/fetch/verification/synthesis/document/total times; tool_count/successful/failed/cache_hits/parallel_batches/maximum_concurrency/Critical_Path_Latency); classify `performance_failure` when `total_time > 30s` and `architecture_failure` when `total_time > 60s`, independent of external-service-degradation state
    - _Requirements: 4.3, 4.4, 4.5, 4.6, 4.7, 4.8_

  - [x] 23.3 Write integration test for a Normal_Request end-to-end
    - Using mocked/high-fidelity-fake Tavily/Jina/Chroma/LLM clients, run one Normal_Request fixture (e.g. unemployed student) through the full orchestrator and assert `tool_count` spans more than 2 distinct tool categories per Requirement 17.1, and that no tool is invoked whose output would not be used per Requirement 17.2
    - _Requirements: 17.1, 17.2, 17.3_

- [x] 24. Checkpoint - Ensure full pipeline integration tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 25. Implement Streaming UI
  - [x] 25.1 Implement the async transport
    - WebSocket or SSE `AsyncTransport` and `StreamingUI.publish(event)`, invoked exclusively from Scheduler/Telemetry state-transition callbacks — never from a timer or polling loop
    - _Requirements: 20.6_

  - [x] 25.2 Implement secret redaction
    - Regex/entropy redaction pass over tool input/output values matching secret categories (API keys, env values, tokens, passwords, connection strings) before publish, showing a withheld-content indicator while preserving the remaining non-secret content; strip hidden system prompts/chain-of-thought content
    - _Requirements: 21.1, 21.2, 21.3_

  - [x] 25.3 Implement real-time status rendering
    - Each displayed operation uses exactly one of `RUNNING`/`COMPLETE`/`FAILED`/`SKIPPED`, published within 500ms of the underlying state transition (start, success, failure, or skip), reflecting actual state only; a stage failure or skip is published as an additional event and never removes previously displayed events
    - _Requirements: 20.1, 20.2, 20.3, 20.4, 20.5, 20.7_

  - [x] 25.4 Implement performance trace rendering
    - Render each trace entry limited to exactly tool name, operation description (≤200 chars), status, elapsed time, and result summary (≤500 chars); no other fields
    - _Requirements: 4.9, 21.4_

  - [x] 25.5 Write property test for trace event safety and field limits
    - **Property 20: Trace events never contain secret-shaped values and always respect field limits**
    - **Validates: Requirements 21.1, 21.2, 21.4**

  - [x] 25.6 Write unit test asserting no timer-driven publish path
    - Assert the `publish` call path contains no `asyncio.sleep`/timer-driven call site and is reachable only from real state-transition callbacks
    - _Requirements: 20.6_

- [x] 26. Checkpoint - Ensure Streaming UI tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [x] 27. Testing completion: error/failure/timeout/cache edge cases
  - [x] 27.1 Write unit tests validating configured timeout ranges across all call types
    - Search_Tool (6-8s), Fetch_Tool (8-10s), generic HTTP (8-10s), Fast_Model (2-4s), Reasoning_Model (15-30s)
    - _Requirements: 18.1, 18.2, 18.3, 18.4, 18.5_

  - [x] 27.2 Write unit tests for recoverable vs. non-recoverable retry classification
    - Cover every `Recoverable_HTTP_Status` (408, 429, 500, 502, 503, 504) plus network errors as retryable, and invalid-URL/access-denied/content-not-found as non-retryable, across Search_Tool and Fetch_Tool
    - _Requirements: 18.6, 18.7, 18.8, 18.9_

  - [x] 27.3 Write unit tests for cache hit/miss behavior across key kinds
    - URL-level, scheme-level, and profile/category-level cache checks per Requirements 12.1-12.3, including the fallback to a fresh operation on miss or expiry
    - _Requirements: 12.1, 12.2, 12.3_

  - [x] 27.4 Write unit test for the all-fetches-failed case
    - Assert the System indicates "no source content could be retrieved" rather than silently proceeding with zero fetched sources when every candidate fetch for a request times out or fails
    - _Requirements: 11.6_

- [x] 28. Accuracy/integration testing across profile types
  - [x] 28.1 Write end-to-end fixtures and tests for 5 profile types
    - Unemployed student, elderly citizen, widow, farmer, small business owner — each run through the full orchestrator (Task 23.1) with mocked/high-fidelity-fake external services, asserting a plausible eligibility result is produced for each
    - _Requirements: 22.2_

  - [x] 28.2 Write test asserting identical pipeline stage sequence across profile types
    - Assert the same ordered sequence of pipeline stages executes for all 5 fixtures from Task 28.1, and for at least one additional scheme/department not present in existing configuration, with only profile/scheme data differing and no code-level branching observed
    - _Requirements: 22.2, 22.3, 22.4_

- [x] 29. Real-world latency validation
  - [x] 29.1 Write latency validation tests for Normal_Request and Complex_Request targets
    - Using realistic mocked service latencies, assert Normal_Request completes within 15s (typical 8-15s) and Complex_Request completes within 25s (typical 15-25s)
    - _Requirements: 4.1, 4.2_

  - [x] 29.2 Write degraded-dependency latency and failure-classification test
    - Simulate an external service (Search_Tool/Fetch_Tool/Fast_Model/Reasoning_Model) exceeding its normal response time and assert the System still returns within 30s; assert `performance_failure` is set when `total_time > 30s` and `architecture_failure` is set when `total_time > 60s`
    - _Requirements: 4.3, 4.4, 4.5_

- [x] 30. Final checkpoint - Ensure all tests pass across the full suite
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional test sub-tasks and can be skipped for a faster MVP; core implementation tasks (unmarked) are never optional.
- Task 7 (concurrency tests) and Task 8 (checkpoint) form a **required blocking gate**: no Phase 3+ task (Task 9 onward) may begin until both concurrency tests pass, per the design's testing strategy and Requirement 3.
- Tasks 14.2, 15.1 introduce interface stubs for `FilterPipeline` and the verification engine that are fully implemented and wired in Tasks 19 and 20 respectively — by the end of Task 20 there is no orphaned/stub code left in the agent layer.
- Property tests validate the 21 universal correctness properties from the design's Correctness Properties section, each placed as close as possible to the implementation it covers; all 21 properties are covered across Tasks 1, 3, 4, 5, 6, 10, 12, 14, 15, 16 (implicitly via 1.5), 19, 20, 21, 22, and 25.
- Unit/integration tests validate concrete edge cases, external-service wiring, and UI trace rendering that are not universal properties.
- Checkpoints ensure incremental validation; multiple checkpoints are placed at natural phase boundaries.

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "1.3", "1.4"] },
    { "id": 2, "tasks": ["1.5", "1.6"] },
    { "id": 3, "tasks": ["3.1"] },
    { "id": 4, "tasks": ["3.2"] },
    { "id": 5, "tasks": ["3.3"] },
    { "id": 6, "tasks": ["4.1", "4.2"] },
    { "id": 7, "tasks": ["4.3"] },
    { "id": 8, "tasks": ["4.4", "4.5", "4.6"] },
    { "id": 9, "tasks": ["5.1", "5.2", "5.3"] },
    { "id": 10, "tasks": ["5.4", "5.5"] },
    { "id": 11, "tasks": ["6.1", "6.2"] },
    { "id": 12, "tasks": ["6.3"] },
    { "id": 13, "tasks": ["6.4"] },
    { "id": 14, "tasks": ["7.1", "7.2"] },
    { "id": 15, "tasks": ["9.1", "9.2", "10.1", "11.1", "12.1", "12.2"] },
    { "id": 16, "tasks": ["11.2", "9.3", "10.2", "10.3", "12.3", "12.4"] },
    { "id": 17, "tasks": ["11.3"] },
    { "id": 18, "tasks": ["14.1", "14.2", "15.1", "15.2", "15.3"] },
    { "id": 19, "tasks": ["16.1"] },
    { "id": 20, "tasks": ["14.3", "14.4", "15.4", "15.5", "16.2"] },
    { "id": 21, "tasks": ["18.1"] },
    { "id": 22, "tasks": ["18.2"] },
    { "id": 23, "tasks": ["19.1"] },
    { "id": 24, "tasks": ["19.2"] },
    { "id": 25, "tasks": ["19.3"] },
    { "id": 26, "tasks": ["19.4"] },
    { "id": 27, "tasks": ["19.5", "19.6"] },
    { "id": 28, "tasks": ["20.1"] },
    { "id": 29, "tasks": ["20.2"] },
    { "id": 30, "tasks": ["20.3"] },
    { "id": 31, "tasks": ["20.4"] },
    { "id": 32, "tasks": ["20.5", "20.6"] },
    { "id": 33, "tasks": ["21.1"] },
    { "id": 34, "tasks": ["21.2"] },
    { "id": 35, "tasks": ["21.3"] },
    { "id": 36, "tasks": ["21.4"] },
    { "id": 37, "tasks": ["22.1"] },
    { "id": 38, "tasks": ["22.2"] },
    { "id": 39, "tasks": ["23.1"] },
    { "id": 40, "tasks": ["23.2"] },
    { "id": 41, "tasks": ["23.3"] },
    { "id": 42, "tasks": ["25.1"] },
    { "id": 43, "tasks": ["25.2"] },
    { "id": 44, "tasks": ["25.3"] },
    { "id": 45, "tasks": ["25.4"] },
    { "id": 46, "tasks": ["25.5", "25.6"] },
    { "id": 47, "tasks": ["27.1", "27.2", "27.3", "27.4"] },
    { "id": 48, "tasks": ["28.1"] },
    { "id": 49, "tasks": ["28.2"] },
    { "id": 50, "tasks": ["29.1", "29.2"] }
  ]
}
```
