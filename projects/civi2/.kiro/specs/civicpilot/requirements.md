# Requirements Document

## Introduction

CivicPilot is a high-speed, multi-tool AI agent system that helps Indian citizens discover and verify their eligibility for government welfare schemes. The system is built from scratch as a multi-agent architecture (CrewAI roles: Planner, Researcher, Verifier, Document Advisor, Reporter) whose actual tool execution is driven by a custom async scheduler rather than CrewAI's default sequential execution model. The system's defining constraint is speed without sacrificing correctness: normal requests must resolve in 8-15 seconds, complex requests in 15-25 seconds, and requests with slow external services in no more than ~30 seconds, achieved through aggressive concurrency, adaptive stopping, deterministic short-circuiting of non-reasoning tasks, and caching. The system must generalize across arbitrary citizen profiles and schemes with no hardcoded scheme-, department-, or user-type-specific logic, and must present its progress and results honestly to the user via a real-time streaming UI without exposing internal secrets or fabricating progress.

## Glossary

- **System**: The CivicPilot application as a whole, encompassing all agents, the scheduler, tool layer, and UI.
- **Planner_Agent**: The CrewAI role responsible for turning a structured profile into a small set of targeted search operations and an identification of which operations are independent.
- **Researcher_Agent**: The CrewAI role responsible for issuing searches and fetching source content via tools.
- **Verifier_Agent**: The CrewAI role responsible for evaluating scheme eligibility criteria against a citizen profile.
- **Document_Advisor_Agent**: The CrewAI role responsible for deciding whether and how to produce an application-support document for a specific scheme.
- **Reporter_Agent**: The CrewAI role responsible for assembling the final user-facing result and performance trace.
- **Scheduler**: The custom async tool scheduler that creates, schedules, prioritizes, executes, retries, times out, cancels, and collects results for tool tasks, independent of CrewAI's built-in orchestration.
- **Fast_Model**: The configured lightweight LLM used for deterministic-adjacent, low-latency tasks (profile extraction, classification, filtering, dedup, routing).
- **Reasoning_Model**: The configured higher-capability LLM used for planning, eligibility reasoning, conflict resolution, and final verification.
- **Intake_Module**: The component that parses free-text citizen input into a structured Profile.
- **Profile**: A structured record of citizen attributes (age, gender, income, location, category, special_status, family_size, education_level, occupation, stated_need) in which unknown fields are null.
- **Search_Tool**: The Tavily-backed web search tool used by the Researcher_Agent.
- **Fetch_Tool**: The Jina Reader-backed page extraction tool used by the Researcher_Agent.
- **Cache_Store**: The ChromaDB-backed (or equivalent) persistence layer used to cache URL content, scheme records, and profile/category lookups.
- **Verification_Module**: The logic (backed by the Verifier_Agent and Reasoning_Model) that classifies each eligibility criterion as PASS, FAIL, or UNKNOWN and derives an overall result.
- **Document_Generator**: The python-docx-backed component that produces application-support documents.
- **Portal_Validator**: The component that determines whether a URL is a confirmed official government domain.
- **Telemetry_Module**: The component that records per-stage timings and concurrency metrics for a request.
- **Streaming_UI**: The user-facing interface that displays real-time status updates as operations complete.
- **Critical_Path_Latency**: The elapsed wall-clock time of a set of concurrently executed operations, measured from the start of the first operation to the completion of the last, not the sum of individual operation durations.
- **Normal_Request**: A citizen request whose profile is well-specified and whose eligibility determination requires no unusual follow-up research.
- **Complex_Request**: A citizen request requiring multiple scheme candidates, additional follow-up searches, or multiple concurrent verifications.
- **Trustworthy_Source**: A source not matching the domain patterns of priority tiers 1 through 5 (myscheme.gov.in, official central government domains, official state government domains, official department portals, official government PDFs) that is nonetheless verifiable as legitimate through indicators such as an established news organization domain, an accredited NGO or educational institution domain, or other verifiable non-commercial, non-spam publisher identity.
- **Recoverable_HTTP_Status**: An HTTP response status code indicating a transient condition safe to retry, specifically 408 (Request Timeout), 429 (Too Many Requests), 500 (Internal Server Error), 502 (Bad Gateway), 503 (Service Unavailable), and 504 (Gateway Timeout).

## Requirements

### Requirement 1: Multi-Agent Architecture with Custom Async Scheduling

**User Story:** As a system architect, I want CivicPilot's five agent roles to be implemented in CrewAI while actual tool execution is governed by a custom async scheduler, so that the system retains a clear separation of reasoning roles without being constrained by CrewAI's default sequential execution.

#### Acceptance Criteria

1. THE System SHALL implement five distinct CrewAI agent roles: Planner_Agent, Researcher_Agent, Verifier_Agent, Document_Advisor_Agent, and Reporter_Agent.
2. THE System SHALL route all tool invocations issued by any agent through the Scheduler rather than through CrewAI's default sequential tool-calling mechanism.
3. WHEN an agent issues two or more tool operations whose inputs and outputs have no data dependency on one another and that do not target the same underlying resource, THE Scheduler SHALL initiate execution of those operations concurrently, such that no operation's start is delayed pending completion of another, up to a maximum number of concurrently executing operations defined by system configuration.
4. THE System SHALL attribute each reasoning output (plan, research finding, verification result, document recommendation, or report content) produced during execution to the specific agent role that generated it, regardless of the fact that the underlying tool execution is centrally scheduled.
5. IF an agent role attempts to invoke a tool through any mechanism other than the Scheduler, THEN THE System SHALL reject that invocation without executing the underlying tool, SHALL return an error indication to the invoking agent identifying the invocation as an architecture violation, and SHALL leave the state of any previously completed operations unchanged.

### Requirement 2: Async Tool Scheduler as First-Class Component

**User Story:** As a system architect, I want a dedicated async tool scheduler that manages the full lifecycle of tool tasks, so that concurrency, reliability, and performance are controlled centrally and consistently across all tools.

#### Acceptance Criteria

1. THE Scheduler SHALL support creation of tool tasks with an associated tool name, input parameters, a numeric priority value between 1 (highest priority) and 10 (lowest priority), a timeout duration specified in milliseconds and greater than zero, and a dependency list.
2. WHEN a tool task's declared dependencies have all completed successfully, THE Scheduler SHALL make that task eligible for execution.
3. THE Scheduler SHALL enforce a configurable maximum concurrency limit, an integer greater than or equal to 1, per tool category and SHALL never execute more concurrent tasks of a category than that limit.
4. WHERE multiple eligible tasks compete for available concurrency slots, THE Scheduler SHALL select the task with the highest priority value first, and SHALL select among tasks of equal priority in the order in which their dependencies became satisfied.
5. WHEN a tool task exceeds its configured timeout, THE Scheduler SHALL cancel that task and set its status to timed_out.
6. IF a tool task fails with an error the tool reports as retryable, THEN THE Scheduler SHALL retry that task at most one additional time before setting its status to failed.
7. WHEN a tool task fails after exhausting its retry allowance, THE Scheduler SHALL isolate that failure such that other running or pending tasks that do not depend on the failed task continue their execution without being cancelled or paused.
8. THE Scheduler SHALL collect the result, status (one of: pending, eligible, running, completed, failed, timed_out, cancelled, or skipped), start time, and end time of every tool task.
9. WHEN the requesting agent requests cancellation of a pending or running tool task, THE Scheduler SHALL stop that task's execution or remove it from the pending queue, and set its status to cancelled.
10. THE Scheduler SHALL record, for each batch of tool tasks that execute concurrently within a tool category, the number of tasks in that batch and the elapsed time measured from the start of the first task in the batch to the completion of the last task in the batch.
11. IF any declared dependency of a tool task fails, times out, or is cancelled, THEN THE Scheduler SHALL set that dependent task's status to skipped without executing it.

### Requirement 3: Mandatory Concurrency Verification Tests

**User Story:** As a system architect, I want automated tests that prove the scheduler achieves genuine concurrent execution, so that performance claims are verified rather than assumed.

#### Acceptance Criteria

1. THE System SHALL include an automated test that schedules 5 independent fake tasks, each configured with an artificial execution duration of exactly 2.0 seconds, allowing a measurement tolerance of ±0.2 seconds.
2. WHEN the 5-task concurrency test executes, THE Scheduler SHALL complete all 5 tasks within a total elapsed wall-clock time of no more than 3.0 seconds, measured from test start to the completion of the last task.
3. IF the 5-task concurrency test's total elapsed wall-clock time exceeds 3.0 seconds, THEN THE System SHALL treat the test as failed and SHALL report the measured elapsed time as failure evidence.
4. THE System SHALL include a second automated test that schedules 4 fake search tasks, 4 fake fetch tasks, and 3 fake verification tasks, each configured with an artificial execution duration between 1.0 and 2.0 seconds, structured so that each fetch task cannot start until its corresponding search task completes, and each verification task cannot start until all fetch tasks it depends on have completed.
5. WHEN the second concurrency test executes, THE Scheduler SHALL execute all independent tasks within each stage concurrently rather than sequentially.
6. WHEN the second concurrency test executes, THE Scheduler SHALL report a measured Critical_Path_Latency, and THE System SHALL treat the test as failed IF the measured Critical_Path_Latency exceeds 120% of the theoretical critical path duration (the longest chain of dependent task durations from search through verification) or exceeds 80% of the summed duration of all 11 tasks, whichever is lower.
7. WHEN either concurrency test executes, THE Telemetry_Module SHALL report the maximum number of tasks observed with overlapping execution windows at any single point in time during that test.

### Requirement 4: End-to-End Performance Targets and Telemetry

**User Story:** As a citizen using CivicPilot, I want responses within a bounded, predictable time, so that the tool feels responsive rather than stalled.

#### Acceptance Criteria

1. WHEN the System processes a Normal_Request under normal external service conditions, THE System SHALL return a final result no later than 15 seconds after request intake, with a typical completion time between 8 and 15 seconds.
2. WHEN the System processes a Complex_Request under normal external service conditions, THE System SHALL return a final result no later than 25 seconds after request intake, with a typical completion time between 15 and 25 seconds.
3. WHILE the measured response time of an external service (Search_Tool, Fetch_Tool, Fast_Model, or Reasoning_Model) exceeds that service's normal response time such that the request's elapsed time surpasses the target defined in Criterion 1 or Criterion 2, THE System SHALL return a final result no later than 30 seconds after request intake.
4. IF total request processing time exceeds 30 seconds measured from request intake, THEN THE System SHALL classify the request as a performance failure in its telemetry output, regardless of the external service condition state described in Criterion 3.
5. IF total request processing time exceeds 60 seconds measured from request intake, THEN THE System SHALL classify the request as an architecture failure in its telemetry output, regardless of the external service condition state described in Criterion 3.
6. THE Telemetry_Module SHALL record, for every request, the intake_time, planning_time, search_time, filtering_time, fetch_time, verification_time, synthesis_time, document_time, and total_time, each expressed in seconds with millisecond precision.
7. THE Telemetry_Module SHALL record, for every request, the tool_count, successful_tool_count, failed_tool_count, cache_hits, parallel_batches, maximum_concurrency, and Critical_Path_Latency.
8. THE Telemetry_Module SHALL compute Critical_Path_Latency from actual measured start and completion timestamps of concurrently executed operations rather than from the sum of individual operation durations.
9. THE System SHALL produce a performance trace, rendered as readable text, describing each tool-invocation batch, including the tool name, execution mode (PARALLEL or SEQUENTIAL), the count of completed operations versus total operations in the batch, and the measured elapsed time of the batch expressed in seconds.

### Requirement 5: Execution Efficiency Principles

**User Story:** As a system architect, I want the system to avoid unnecessary work at every stage, so that speed targets are met without over-provisioning hardware or cutting correctness corners.

#### Acceptance Criteria

1. IF two or more planned operations (LLM calls, searches, or fetches) have no data dependency between them, THEN THE System SHALL execute those operations concurrently.
2. IF one of two or more concurrently executed operations fails, THEN THE System SHALL ensure the failure does not block or delay the completion of the other independent operations that have no dependency on the failed operation.
3. WHEN the evidence collected so far satisfies the confidence threshold defined for reaching an eligibility determination, THE System SHALL skip any remaining planned searches, fetches, or verification calls for that determination and finalize the determination using only the evidence already collected.
4. IF a required piece of data is present in the Cache_Store and is not expired or marked invalid under the Cache_Store's validity rules, THEN THE System SHALL use the cached data instead of issuing a redundant LLM call, search, or fetch.
5. THE System SHALL NOT invoke Fast_Model or Reasoning_Model for any task that can be resolved deterministically, including URL domain detection, deduplication, cache-key lookup, timeout and retry handling, sorting, and input validation.
6. IF a task's outcome is deterministic, THEN THE System SHALL implement that task using deterministic code rather than invoking Fast_Model or Reasoning_Model.

### Requirement 6: Two-Tier Model Routing

**User Story:** As a system operator, I want fast and reasoning-heavy LLM tasks routed to differently-capable, independently configurable models, so that latency-sensitive tasks stay fast and correctness-critical tasks stay accurate.

#### Acceptance Criteria

1. THE System SHALL define a Fast_Model role used for profile extraction, classification, filtering, deduplication assistance, and routing decisions.
2. THE System SHALL define a Reasoning_Model role used for planning, eligibility reasoning, conflict resolution, and final verification decisions.
3. WHEN the System starts, THE System SHALL load, for each of the Fast_Model role and the Reasoning_Model role, a model identifier and connection parameters (including a provider endpoint and an authentication credential) from the environment configuration (.env).
4. IF the environment configuration is missing, or contains invalid values for, the model identifier or connection parameters of the Fast_Model role or the Reasoning_Model role at startup, THEN THE System SHALL fail to start and SHALL produce an error indication identifying which role's configuration is missing or invalid.
5. WHERE an operator supplies different provider configuration values for the Fast_Model role or the Reasoning_Model role in the environment configuration, THE System SHALL, upon the next startup, use the newly configured provider for the corresponding role without requiring any source code modification.
6. WHEN a task is assigned to the Fast_Model role, THE System SHALL invoke Fast_Model to perform that task.
7. WHEN a task is assigned to the Reasoning_Model role, THE System SHALL invoke Reasoning_Model to perform that task.
8. THE System SHALL maintain the role-to-model mapping established at startup for the entire runtime of the System process, such that the mapping SHALL NOT change except by restarting the System with updated environment configuration.

### Requirement 7: Fast Intake and Profile Extraction

**User Story:** As a citizen, I want to describe my situation in free text and have the system understand my relevant attributes, so that I do not have to fill out a rigid form.

#### Acceptance Criteria

1. WHEN a citizen submits free-text input of up to 5,000 characters, THE Intake_Module SHALL parse that input into a structured Profile containing age, gender, income, location, category, special_status, family_size, education_level, occupation, and stated_need fields.
2. IF the citizen's free-text input is empty, whitespace-only, or exceeds 5,000 characters, THEN THE Intake_Module SHALL reject the input, return an error indication describing the reason for rejection, and SHALL NOT create a Profile.
3. IF a Profile field cannot be determined from the citizen's input, THEN THE Intake_Module SHALL set that field to null rather than inferring a default or guessed value.
4. THE Intake_Module SHALL never populate a Profile field with a value that was not explicitly stated in the citizen's input or directly derivable from explicitly stated facts through direct computation or unambiguous mapping (e.g., computing age from a stated birth date, or family_size from an explicitly stated list of household members).
5. WHEN extracting a Profile from free-text input, THE Intake_Module SHALL use Fast_Model with a structured JSON output format for the extraction call.
6. IF the Fast_Model extraction call fails or returns output that cannot be parsed as valid structured JSON, THEN THE Intake_Module SHALL return an error indication to the caller rather than a partial or malformed Profile, and SHALL NOT persist that Profile.

### Requirement 8: Planning

**User Story:** As a system architect, I want a lightweight planning step that turns a profile into a small set of targeted searches, so that research effort stays proportional and fast.

#### Acceptance Criteria

1. WHEN a Profile has been produced by the Intake_Module, THE Planner_Agent SHALL generate 3 to 5 search operations, where each search operation SHALL reference at least one attribute from the Profile and SHALL be distinct from every other search operation in the set (no two search operations SHALL have identical search parameters).
2. THE Planner_Agent SHALL issue exactly one planning call to Reasoning_Model per request to produce the search plan.
3. IF the planning call to Reasoning_Model fails, does not return a response within 30 seconds, or returns a search plan containing fewer than 3 or more than 5 search operations, THEN THE Planner_Agent SHALL discard the returned plan and SHALL indicate a planning failure to the calling process without producing a partial search plan.
4. THE Planner_Agent SHALL identify, among the generated search operations, which operations have no dependency on one another, where a search operation is considered dependent on another search operation if it requires data produced as output by that other search operation before it can execute.
5. WHEN the Planner_Agent has identified a set of independent search operations, THE Scheduler SHALL execute all search operations in that set concurrently.
6. WHEN the Planner_Agent has identified search operations that are dependent on other search operations, THE Scheduler SHALL execute each dependent search operation only after all search operations it depends on have completed.
7. IF a search operation fails during execution, THEN THE Scheduler SHALL continue executing the remaining independent search operations and SHALL record the failed search operation's status as failed without halting execution of the overall search plan.

### Requirement 9: Search Strategy and Source Prioritization

**User Story:** As a citizen, I want the system to search official and trustworthy sources first, so that the schemes it surfaces are legitimate and current.

#### Acceptance Criteria

1. THE Researcher_Agent SHALL use the Search_Tool to issue 3 to 5 search queries per request, where each query is distinct from the other queries issued for that request and is derived from details stated in the request (such as scheme name, sector, beneficiary category, or location).
2. THE Researcher_Agent SHALL include, among the issued search queries, at least one query scoped to the site:myscheme.gov.in domain.
3. WHEN multiple search queries issued for a request have no dependency on one another (i.e., no query requires the result of another query as input), THE Scheduler SHALL execute those queries concurrently, subject to a configured maximum search concurrency of 1 to 5 concurrent queries, with a default of 4.
4. WHEN a search query issued for a request depends on the result of another query issued for that same request, THE Scheduler SHALL execute the dependent query only after the query it depends on has completed.
5. THE System SHALL rank candidate sources for a scheme in the following priority order: (1) myscheme.gov.in, (2) official central government domains, (3) official state government domains, (4) official department portals, (5) official government PDFs, (6) other trustworthy sources, where a candidate source SHALL be classified into priority tier 6 ("other trustworthy sources") only if it does not match the domain pattern of tiers 1 through 5 and satisfies the Trustworthy_Source definition in the Glossary.

### Requirement 10: Search Result Filtering Pipeline

**User Story:** As a system architect, I want search results filtered down to the strongest candidates before any expensive fetch occurs, so that fetch and verification effort is not wasted on weak or duplicate sources.

#### Acceptance Criteria

1. WHEN raw search results are received, THE System SHALL deduplicate result URLs using deterministic logic rather than an LLM call, treating two URLs as duplicates when they normalize to the same address (ignoring differences in protocol, trailing slash, and query parameter ordering).
2. WHEN raw search results are received, THE System SHALL remove, using deterministic logic rather than an LLM call, any result whose title and snippet contain no overlap with the search query's terms or topic.
3. IF, after the filtering in Acceptance Criteria 1 and 2, one or more candidate results remain whose relevance to the search query cannot be conclusively determined by deterministic logic, THEN THE System SHALL use Fast_Model to classify the relevance of those remaining candidates.
4. IF the Fast_Model relevance classification described in Acceptance Criterion 3 fails to return a result, THEN THE System SHALL retain the affected candidates in the selection pool without a Fast_Model relevance classification and proceed to ranking and selection using only the relevance information available from deterministic filtering.
5. WHEN candidate sources remain after the filtering described in Acceptance Criteria 1 through 4, THE System SHALL rank the remaining candidates by relevance to the search query and select the top 5 for fetching, selecting all remaining candidates instead when fewer than 5 remain, including cases where fewer than 3 candidates remain.
6. WHEN the candidate sources have been selected per Acceptance Criterion 5, THE Scheduler SHALL fetch those sources concurrently, not exceeding the configured maximum fetch concurrency.

### Requirement 11: Source Fetching and Failure Isolation

**User Story:** As a citizen, I want a single slow or broken source to never stall my entire request, so that I still get a timely answer even when some websites misbehave.

#### Acceptance Criteria

1. THE Researcher_Agent SHALL use the Fetch_Tool (Jina Reader) to extract content from selected candidate source pages.
2. THE Scheduler SHALL execute Fetch_Tool operations at a concurrency level equal to the configured maximum fetch concurrency, where the configured maximum fetch concurrency SHALL be set to a value between 3 and 5 concurrent fetches inclusive.
3. THE Scheduler SHALL apply a per-operation timeout, set to a fixed value between 8 and 10 seconds inclusive, applied consistently to every Fetch_Tool operation within a single research request.
4. IF a Fetch_Tool operation fails with a recoverable error (such as a connection timeout, network error, or server error response), THEN THE Scheduler SHALL retry that operation at most one additional time.
5. IF a Fetch_Tool operation times out, fails with a non-recoverable error (such as an invalid URL, access denied response, or content-not-found response), or fails after its retry allowance is exhausted, THEN THE Scheduler SHALL exclude that source from further processing without blocking or delaying the completion of other concurrent fetch operations.
6. IF all candidate source fetch operations for a request are excluded due to timeout or failure, THEN THE Scheduler SHALL indicate that no source content could be retrieved for that request rather than proceeding with zero fetched sources.

### Requirement 12: Caching

**User Story:** As a system architect, I want previously retrieved scheme and page data cached, so that repeated or similar requests avoid redundant expensive operations.

#### Acceptance Criteria

1. WHEN the System needs content for a specific URL, THE System SHALL check the Cache_Store for an exact URL cache entry before issuing a Fetch_Tool operation for that URL. IF a matching cache entry exists and has not been excluded as expired, THEN THE System SHALL use the cached content payload instead of issuing a new Fetch_Tool operation. IF no matching cache entry exists, or the matching entry has been excluded as expired, THEN THE System SHALL issue a Fetch_Tool operation for that URL.
2. WHEN the System needs information about a scheme, THE System SHALL check the Cache_Store for a scheme-level cache entry before issuing new search or fetch operations for that scheme. IF a matching scheme-level cache entry exists and has not been excluded as expired, THEN THE System SHALL use the cached entry instead of issuing new search or fetch operations. IF no matching scheme-level cache entry exists, or the matching entry has been excluded as expired, THEN THE System SHALL issue new search or fetch operations for that scheme.
3. WHEN the System needs information relevant to a citizen's profile category, THE System SHALL check the Cache_Store for a profile or category cache entry before issuing new search operations for that category. IF no matching profile or category cache entry exists, or the matching entry has been excluded as expired, THEN THE System SHALL issue new search operations for that category. IF a matching profile or category cache entry exists and has not been excluded as expired, THEN THE System SHALL use the cached entry instead of issuing new search operations.
4. THE Cache_Store SHALL record, for each cached entry, a timestamp, source identifier, a status of one of "success", "partial", or "failure", a confidence level expressed as a numeric value between 0.0 and 1.0 inclusive, and a content payload.
5. IF a result was produced from an operation with a status of "failure" or "partial", THEN THE System SHALL NOT store that result in the Cache_Store.
6. THE System SHALL consider a cache entry expired when its age exceeds 24 hours measured from its recorded timestamp.
7. THE System SHALL exclude expired cache entries from the cache-hit checks defined in criteria 1 through 3.

### Requirement 13: Adaptive Execution and Evidence Sufficiency

**User Story:** As a citizen, I want the system to stop researching once it has enough evidence to answer my question, so that I am not kept waiting for redundant work.

#### Acceptance Criteria

1. WHEN a research or verification stage completes for an eligibility determination, THE System SHALL assess whether the evidence gathered so far is sufficient, where evidence is sufficient when every eligibility criterion applicable to the determination has resolved evidence (confirmed met, confirmed not met, or confirmed inconclusive after applicable checks) and no unresolved conflict among the gathered evidence would change the determination outcome.
2. IF gathered evidence is assessed as sufficient, THEN THE System SHALL skip remaining planned searches, fetches, or verification operations for that determination and SHALL indicate, as part of the determination output, which planned operations were skipped.
3. IF gathered evidence is assessed as insufficient, THEN THE System SHALL perform only the additional searches, fetches, or verification operations targeting the specific eligibility criteria whose evidence remains unresolved, and SHALL repeat the sufficiency assessment after each additional operation.
4. THE System SHALL limit the number of additional evidence-gathering cycles triggered by insufficiency to a maximum of 5 cycles per determination.
5. IF evidence remains insufficient after the maximum number of additional cycles, THEN THE System SHALL produce a determination indicating that the eligibility outcome could not be confidently established, and SHALL identify which eligibility criteria remain unresolved.

### Requirement 14: Eligibility Verification

**User Story:** As a citizen, I want an honest determination of whether I qualify for a scheme, including cases where the system cannot be sure, so that I am not misled into applying for something I do not qualify for or missing something I do.

#### Acceptance Criteria

1. WHEN the Verifier_Agent evaluates a scheme against a Profile, THE Verifier_Agent SHALL classify each individual eligibility criterion as PASS, FAIL, or UNKNOWN.
2. WHEN the Verifier_Agent completes classification of all individual eligibility criteria for a scheme against a Profile, THE Verifier_Agent SHALL derive an overall eligibility result as follows: ELIGIBLE if every criterion is classified as PASS; NOT ELIGIBLE if at least one criterion is classified as FAIL; POSSIBLE-NEEDS INFO if no criterion is classified as FAIL and at least one criterion is classified as UNKNOWN.
3. IF a Profile field required to evaluate a criterion is null, missing, or otherwise unavailable, THEN THE Verifier_Agent SHALL classify that criterion as UNKNOWN and SHALL NOT assume a default value for the missing field or classify the criterion as PASS.
4. IF a criterion cannot be resolved through direct comparison of Profile field values against scheme-defined thresholds or enumerated values, THEN THE Verifier_Agent SHALL use Reasoning_Model to perform the classification of that criterion.
5. IF the Reasoning_Model does not return a definitive PASS or FAIL determination for a criterion, including cases where it returns an inconclusive result or fails to respond, THEN THE Verifier_Agent SHALL classify that criterion as UNKNOWN.
6. WHEN two or more scheme verifications against the same Profile have no dependency on one another, THE Scheduler SHALL execute those verifications concurrently, subject to the configured maximum verification concurrency.

### Requirement 15: Document Generation

**User Story:** As a citizen who is confidently eligible for a specific scheme, I want application-support documentation prepared for me, so that I can act on my eligibility result without redoing research myself.

#### Acceptance Criteria

1. WHEN a scheme's overall eligibility result is ELIGIBLE at the high-confidence level defined by the eligibility determination process, and the scheme's identity information and application information required for that scheme are fully available, THE Document_Advisor_Agent SHALL generate an application-support document for that scheme using the Document_Generator (python-docx).
2. IF a scheme's overall eligibility result is not ELIGIBLE at the high-confidence level defined by the eligibility determination process, or the scheme's identity information and application information required for that scheme are not fully available, THEN THE Document_Advisor_Agent SHALL NOT generate an application-support document for that scheme.
3. THE Document_Generator SHALL populate each generated document using only the criteria and information belonging to the specific scheme being documented, and SHALL NOT include criteria or information from any other scheme.
4. THE System SHALL deliver the eligibility result to the citizen without waiting for application-support document generation to complete for that scheme.
5. IF document generation for a scheme fails for any reason, THEN THE Document_Advisor_Agent SHALL retain the eligibility result as valid, SHALL NOT block or alter delivery of the eligibility result to the citizen, and SHALL provide an indication that the application-support document is unavailable for that scheme.

### Requirement 16: Official Portal Validation

**User Story:** As a citizen, I want to know clearly whether a link I'm given is an official government source, so that I do not mistake a commercial or unofficial site for a government portal.

#### Acceptance Criteria

1. WHEN the Portal_Validator evaluates a domain associated with a link presented to the citizen, THE Portal_Validator SHALL confirm the domain as an official government portal only if the domain's suffix exactly matches ".gov.in", ".nic.in", or a domain explicitly listed in the System's maintained registry of verified official state government domains.
2. THE Portal_Validator SHALL NOT present any domain that does not meet the matching criteria defined in Criterion 1 as an official government portal.
3. IF the Portal_Validator confirms a domain as an official government portal, THEN THE System SHALL include, in the same response as the link, a statement that explicitly identifies the source as an official government portal.
4. IF the Portal_Validator cannot confirm a domain as an official government portal, THEN THE System SHALL include, in the same response as the link, a statement that explicitly indicates the portal's official status could not be confirmed, without blocking the citizen's access to the link.

### Requirement 17: Multi-Tool Usage and Tool Budget

**User Story:** As a system architect, I want the system to make genuine, proportional use of multiple tools per request, so that tool usage reflects actual need rather than being padded for appearance.

#### Acceptance Criteria

1. WHERE a request requires at least one Search_Tool, Fetch_Tool, or Verification_Module operation beyond a Cache_Store lookup (that is, the request cannot be fully resolved from cached data alone), THE System SHALL use more than 2 distinct tool categories, selected from Fast_Model, Reasoning_Model, Search_Tool, Fetch_Tool, Cache_Store, Portal_Validator, Document_Generator, or other configured APIs, for that request.
2. IF a tool's output would not be used to fill a missing Profile field, satisfy an evidence-sufficiency determination (Requirement 13), evaluate an eligibility criterion (Requirement 14), confirm a source's official status (Requirement 16), or produce a qualifying application-support document (Requirement 15), THEN THE System SHALL NOT invoke that tool for the current request.
3. WHEN processing a Normal_Request, THE System SHALL issue approximately 3 to 4 searches, 2 to 5 fetches, and 1 to 3 verifications, consistent with the search-operation counts defined in Requirements 8 and 9.
4. WHEN processing a Complex_Request, THE System SHALL issue up to approximately 5 searches, 5 fetches, and 4 verifications.

### Requirement 18: Timeout and Retry Policy

**User Story:** As a system architect, I want every external call bounded by a timeout and a limited retry policy, so that a single slow dependency cannot silently blow past the system's performance targets.

#### Acceptance Criteria

1. THE Scheduler SHALL apply a configurable timeout of no less than 6 seconds and no more than 8 seconds to every Search_Tool operation.
2. THE Scheduler SHALL apply a configurable timeout of no less than 8 seconds and no more than 10 seconds to every Fetch_Tool operation.
3. THE Scheduler SHALL apply a configurable timeout of no less than 8 seconds and no more than 10 seconds to every generic HTTP call.
4. THE Scheduler SHALL apply a configurable timeout of no less than 2 seconds and no more than 4 seconds to every Fast_Model call.
5. THE Scheduler SHALL apply a configurable timeout of no less than 15 seconds and no more than 30 seconds to every Reasoning_Model call.
6. WHEN an external call exceeds its configured timeout, THE Scheduler SHALL treat the call as failed and proceed according to the retry policy defined in this section.
7. IF a failed call is classified as recoverable (transient network error, temporary service unavailability, connection reset, or a Recoverable_HTTP_Status as defined in the Glossary), THEN THE Scheduler SHALL retry that call exactly once using the same timeout applied to the original attempt.
8. IF a failed call is not classified as recoverable, THEN THE Scheduler SHALL NOT retry that call.
9. IF a call fails after its retry allowance is exhausted, THEN THE System SHALL mark the corresponding operation as failed, exclude its result from further processing, and continue processing the remainder of the request without halting on the failure.

### Requirement 19: Failure Isolation and Reliability

**User Story:** As a citizen, I want the system to keep working and give me a usable answer even if some part of it fails, so that a single broken dependency does not ruin my whole session.

#### Acceptance Criteria

1. IF one or more tools or sources fail during a request, THEN THE System SHALL NOT terminate the request with an unhandled error or fail to return any response, and SHALL return a completed response to the citizen within the applicable performance target defined in Requirement 4.
2. WHEN a tool or source failure occurs and at least one tool or source relevant to the request still succeeds, THE System SHALL continue processing using the data obtained from the successful tools and sources and SHALL produce a result based on that remaining data.
3. IF all tools or sources required to reach an eligibility determination for a scheme fail, THEN THE Verifier_Agent SHALL classify the affected criteria as UNKNOWN and THE System SHALL report that scheme's overall eligibility result as POSSIBLE-NEEDS INFO rather than fabricating a determination.
4. IF a result is produced using data affected by one or more tool or source failures, THEN THE System SHALL report a confidence level for that result that is lower than the confidence level it would report absent the failure, and SHALL make this reduced confidence level visible to the citizen in the delivered result.

### Requirement 20: Streaming UI with Real-Time Status

**User Story:** As a citizen, I want to see real progress while my request is being processed, so that I trust the system is genuinely working and know roughly what stage it is in.

#### Acceptance Criteria

1. WHEN a processing stage begins, THE Streaming_UI SHALL display a status update reflecting that the stage has started (for example, "Analyzing request...", "Searching...", "Fetching...", "Verifying...") within 500 milliseconds of the stage beginning.
2. WHEN a processing stage completes successfully, THE Streaming_UI SHALL display a status update reflecting the actual outcome of that stage (for example, "Profile extracted", "Plan created", "N candidates found", "Complete — X.Xs") within 500 milliseconds of the stage completing.
3. THE Streaming_UI SHALL represent each displayed operation using one of the states RUNNING, COMPLETE, FAILED, or SKIPPED, reflecting the operation's actual execution state.
4. IF a processing stage fails, THEN THE Streaming_UI SHALL display a status update indicating that the stage failed, without discarding the status updates already displayed for previously completed stages.
5. WHEN a processing stage is skipped, THE Streaming_UI SHALL display a status update indicating that the stage was skipped.
6. THE Streaming_UI SHALL NOT display simulated or timer-based progress indicators that do not correspond to an actual state transition in the System.
7. THE Streaming_UI SHALL update each status display within 500 milliseconds of the actual completion, failure, or skipping of the operation it represents, rather than at a fixed or artificial interval.

### Requirement 21: Security and Trace Confidentiality

**User Story:** As a system operator, I want the visible execution trace to never leak secrets or internal reasoning, so that the system remains safe to demo and operate without exposing sensitive configuration.

#### Acceptance Criteria

1. THE Streaming_UI SHALL NOT display secret values in any trace entry, tool input, or tool output shown to the user, including API keys, environment variable values, authentication tokens, passwords, and connection strings.
2. IF a tool input or tool output contains a value matching one of the secret categories listed in Criterion 1, THEN THE Streaming_UI SHALL redact that value before display, show an indicator that content was withheld, and preserve the remaining non-secret content of the trace entry.
3. THE Streaming_UI SHALL NOT display hidden system prompts, model chain-of-thought text, or other internal reasoning or planning content generated by the underlying model that is not one of the fields specified in Criterion 4.
4. THE Streaming_UI SHALL limit each trace entry to the following fields only: tool name, operation description (maximum 200 characters), status, elapsed time, and a result summary (maximum 500 characters).

### Requirement 22: Generalization Without Hardcoding

**User Story:** As a citizen with any life situation, I want the system to reason about my eligibility using the same general logic used for everyone else, so that the system works fairly regardless of my specific profile or the scheme in question.

#### Acceptance Criteria

1. THE System SHALL evaluate eligibility using reasoning logic that contains no identifier-based conditional branches keyed on a specific scheme name, department name, document name, URL, state, or user-type; eligibility SHALL instead be determined through data-driven comparisons against profile attribute values supplied as input.
2. THE System SHALL apply the same reasoning engine and pipeline, executing an identical sequence of processing stages, to every citizen profile regardless of profile type, including but not limited to an unemployed student, an elderly citizen, a widow, a farmer, and a small business owner, such that only the profile attribute values supplied as input differ between profiles.
3. THE System SHALL apply the same search, filtering, fetching, verification, and document-generation pipeline, executing an identical sequence of stages, to every scheme and government department, such that only scheme-specific or department-specific parameters (for example, search terms, source locations, or document requirements) supplied as configuration data differ.
4. WHEN a scheme, government department, or citizen profile type that is not already covered by existing configuration is introduced, THE System SHALL process it using the same reasoning engine and pipeline described in Criteria 1 through 3, without requiring modification to the reasoning logic or pipeline code, using only additional configuration or profile data.

### Requirement 23: Configuration Management

**User Story:** As a system operator, I want key operational parameters configurable via environment variables, so that I can tune concurrency and model selection without modifying code.

#### Acceptance Criteria

1. WHEN the System starts, THE System SHALL load MAX_SEARCH_CONCURRENCY from environment configuration, using a default value of 4 if the environment variable is not set.
2. WHEN the System starts, THE System SHALL load MAX_FETCH_CONCURRENCY from environment configuration, using a default value of 4 if the environment variable is not set.
3. WHEN the System starts, THE System SHALL load MAX_VERIFY_CONCURRENCY from environment configuration, using a default value of 3 if the environment variable is not set.
4. IF MAX_SEARCH_CONCURRENCY, MAX_FETCH_CONCURRENCY, or MAX_VERIFY_CONCURRENCY is set to a value that is not an integer between 1 and 32 inclusive, THEN THE System SHALL reject the invalid value, SHALL use that parameter's default value instead, and SHALL record a startup warning indicating which environment variable was invalid.
5. THE Scheduler SHALL enforce the configured MAX_SEARCH_CONCURRENCY, MAX_FETCH_CONCURRENCY, and MAX_VERIFY_CONCURRENCY values as hard upper bounds on concurrent operations for the search, fetch, and verify tool categories respectively, and SHALL NOT permit concurrent operations in any of these categories to exceed its applicable configured limit at any time.
6. THE System SHALL load Fast_Model and Reasoning_Model provider configuration from environment configuration as described in Requirement 6.
