"""CivicPilot: a multi-agent system that helps Indian citizens discover and
verify their eligibility for government welfare schemes.

Package layout (flat layout, rooted at the repository root):

- civicpilot.scheduler:   the custom async ToolTask scheduler (concurrency,
                          priority, dependency graph, timeout/retry, cancel).
- civicpilot.tools:       thin proxy wrappers around external services
                          (Tavily search, Jina fetch, ChromaDB cache,
                          Fast_Model/Reasoning_Model clients, python-docx
                          document generation, Portal_Validator) that submit
                          work exclusively through the Scheduler.
- civicpilot.agents:      the five CrewAI agent roles (Planner, Researcher,
                          Verifier, Document_Advisor, Reporter) that reason
                          about the request but never call tools directly.
- civicpilot.pipeline:    orchestration logic that wires the agents and
                          tools together end to end (intake, filtering,
                          verification engine, adaptive evidence loop,
                          top-level orchestrator).
- civicpilot.ui:          the Streaming_UI transport and trace rendering.
- civicpilot.telemetry:   per-stage timing, concurrency, and Critical_Path
                          Latency instrumentation.
- civicpilot.config:      environment configuration loading and validation.

A flat layout (`civicpilot/` directly at the repository root, rather than
under `src/`) is used for this project: it keeps editable installs and
tooling configuration simple for a single-package project with no plans to
ship multiple distributable packages from one repository.
"""

__version__ = "0.1.0"
