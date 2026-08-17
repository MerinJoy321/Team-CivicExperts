# CivicPilot

## Team CivicExperts

- Aibel Bejoy - S3 CS DS
- Merin Joy - S5 CS C
- Sijil Saju - S3 CS DS

## 🚀 Live Demo

**Production:** https://civicpilot-7ggh5eord-merin-joys-projects.vercel.app

**Alias:** https://civicpilot-gamma.vercel.app

---

## What is CivicPilot?

CivicPilot is an **agentic AI welfare-navigation system for Indian citizens**.

A user describes their situation in plain language. CivicPilot searches relevant government sources, verifies eligibility, identifies missing documents and prerequisites, finds the official application route, and prepares an application draft.

The key idea is:

> **Eligibility is not the same as application readiness.**

A citizen may qualify for a scheme but still be unable to apply because required documents, evidence, certificates, or the correct application route are missing.

---

## Problem

Citizens often struggle to:

- Find government schemes relevant to them
- Understand complicated eligibility rules
- Verify whether they actually qualify
- Know which documents prove eligibility
- Identify prerequisite certificates or services
- Find the correct official application portal
- Know what to do next

A simple chatbot can answer "Am I eligible?" but may not explain **why**, **what evidence is needed**, or **whether the citizen is ready to apply**.

---

## Solution

CivicPilot takes the user through:

```text
Citizen Situation
      ↓
Plan
      ↓
Search Government Sources
      ↓
Find Candidate Schemes
      ↓
Verify Eligibility
      ↓
Find Evidence & Documents
      ↓
Identify Prerequisites
      ↓
Check Application Readiness
      ↓
Find Official Application Portal
      ↓
Generate Application Draft
```

CivicPilot does **not** submit the application on behalf of the citizen. Final submission remains on the official government portal.

---

## Key Features

### 🔎 Scheme Discovery
Dynamically searches government sources instead of relying only on a hardcoded scheme list.

### ✅ Criterion-Level Verification
Checks individual eligibility conditions as:

- `PASS`
- `FAIL`
- `UNCERTAIN`

Each decision can include its reason and supporting source.

### 📚 Evidence Mapping

```text
Government Criterion
        ↓
User Information
        ↓
Supporting Evidence
        ↓
Verification Result
```

### 📄 Document & Prerequisite Analysis

Identifies:

- Available documents
- Missing documents
- Missing information
- Required certificates
- Prerequisite services
- Recommended next actions

### 🟢 Application Readiness

CivicPilot distinguishes between:

```text
ELIGIBLE
   ≠
READY TO APPLY
```

### 🌐 Official Application Route

Finds the official application portal and explains what the citizen should prepare.

### 📝 Application Draft

Generates a structured `.docx` application draft for the user to review.

### 👁️ Agent Execution Trace

The UI exposes the major planning, tool, verification, and decision steps so the process is transparent.

---

## Custom Agent Orchestration

CivicPilot is **agentic without relying on CrewAI or LangGraph**.

The project uses a custom Python orchestration layer with five specialized roles:

| Agent | Responsibility |
|---|---|
| **PlannerAgent** | Creates a validated research plan |
| **ResearcherAgent** | Searches, fetches, filters and ranks scheme information |
| **VerifierAgent** | Checks eligibility criteria against the user profile |
| **DocumentAdvisorAgent** | Identifies documents, evidence and prerequisites |
| **ReporterAgent** | Produces the final result and reporting information |

Supporting orchestration components:

- **Scheduler** - coordinates asynchronous work and dependencies
- **ToolTask** - represents executable tool/model operations
- **ModelClient** - handles model calls

### How they cooperate

```text
User Profile
     ↓
PlannerAgent
     ↓
SearchPlan
     ↓
ResearcherAgent
     ↓
Candidate Schemes
     ↓
VerifierAgent
     ↓
Eligibility + Evidence
     ↓
DocumentAdvisorAgent
     ↓
Documents + Prerequisites
     ↓
ReporterAgent
     ↓
Final Readiness Report
```

The overall execution pattern is:

```text
Plan → Act → Observe → Verify → Replan → Report
```

This custom orchestration is the actual agent framework used by CivicPilot.

---

## Technology

### AI

- Groq-hosted models
- Separate fast and reasoning model roles
- Structured model calls for planning and verification

### Backend

- Python 3.11+
- FastAPI
- Uvicorn
- Custom Python agent orchestration

### Tools

| Technology | Purpose |
|---|---|
| **Tavily** | Web/search discovery |
| **Jina Reader** | Webpage/document retrieval |
| **ChromaDB** | Session-level memory/cache |
| **data.gov.in** | Optional official open-data cross-reference |
| **python-docx** | Application draft generation |

### Frontend

- HTML
- CSS
- JavaScript
- Live agent activity/trace

---

## RAG & Memory

CivicPilot can retrieve and reuse relevant scheme information during a session.

```text
Government Source
      ↓
Tavily / Jina
      ↓
Relevant Content
      ↓
ChromaDB
      ↓
Semantic Retrieval
      ↓
Model Context
      ↓
Verification
```

The current memory is **session-level**. CivicPilot does not maintain a permanent citizen profile.

---

## Project Structure

```text
civi2/
├── api/
│   └── index.py
├── civicpilot/
│   ├── agents/
│   ├── scheduler/
│   ├── tools/
│   ├── pipeline/
│   ├── telemetry/
│   └── web/
│       ├── server.py
│       └── static/
├── tests/
├── pyproject.toml
├── .env.example
├── .gitignore
├── run.py
└── vercel.json
```

`.venv/` and `.env` are local-only and should not be committed.

---

## Running Locally

### Install

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
```

### Configure

Copy `.env.example` to `.env` and add the required API/model credentials.

### Start

```powershell
.\.venv\Scripts\python.exe run.py
```

Open:

```text
http://127.0.0.1:8000
```

---

## Deployment

CivicPilot is deployed as a Python serverless application on Vercel.

```text
Browser
   ↓
Vercel
   ↓
api/index.py
   ↓
FastAPI
   ↓
CivicPilot Agent System
```

Deploy from the project root:

```powershell
vercel --prod
```

Production secrets should be configured as Vercel Environment Variables.

---

## Why CivicPilot Is Agentic

CivicPilot does more than generate a single LLM response.

It:

```text
Understands the user
      ↓
Plans what to investigate
      ↓
Uses external tools
      ↓
Observes retrieved information
      ↓
Verifies eligibility
      ↓
Finds missing evidence
      ↓
Traces prerequisites
      ↓
Determines application readiness
      ↓
Finds the official route
      ↓
Generates an application draft
```

The system therefore combines **specialized agents, tools, state, scheduling, verification and multi-step decision-making** to produce an actionable result.

---

## Limitations

- Currently focused on Indian welfare schemes
- Government rules and portals can change
- Results depend on the availability and quality of authoritative sources
- The system does not submit applications automatically
- Application drafts require user review
- Memory is currently session-level
- Full runs may take several minutes because multiple tool and model calls can be required


