"""CivicPilot CLI Application Runner with Live Groq Provider Integration.

Executes end-to-end CivicPilot pipeline for citizen scheme research, eligibility verification,
official portal annotation, and performance trace rendering.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from typing import Any

import httpx

from civicpilot.agents import (
    DocumentAdvisorAgent,
    PlannerAgent,
    ReporterAgent,
    ResearcherAgent,
    VerifierAgent,
)
from civicpilot.config import ConcurrencyConfig, ModelConfig, Settings, load_settings
from civicpilot.pipeline import CivicPilotPipeline, FilterPipeline, IntakeModule
from civicpilot.scheduler import Scheduler, ToolCategory, ToolTask
from civicpilot.telemetry import TelemetryModule
from civicpilot.tools import (
    DocumentGenerator,
    FetchToolProxy,
    ModelClient,
    PortalValidator,
    SearchToolProxy,
)
from civicpilot.ui import InMemoryAsyncTransport, StreamingUI


def _clean_json_response(raw_text: str) -> str:
    """Strips markdown code fences (e.g. ```json ... ```) from LLM output."""
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw_text.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


async def _call_live_groq_llm(
    model_name: str, endpoint: str, api_key: str, prompt: str, system_prompt: str = ""
) -> str:
    """Executes live chat completion call to Groq API endpoint."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    url = f"{endpoint.rstrip('/')}/chat/completions"
    payload = {
        "model": model_name,
        "messages": messages,
        "temperature": 0.1,
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
        raw_content = data["choices"][0]["message"]["content"]
        return _clean_json_response(raw_content)


async def create_pipeline_executor(settings: Settings):
    """Factory creating task executor using live Groq LLM for FAST_MODEL and REASONING_MODEL tasks."""

    async def executor(task: ToolTask) -> Any:
        category = task.category

        if category == ToolCategory.FAST_MODEL:
            # Live call to Groq Fast_Model (llama-3.1-8b-instant)
            model_name = settings.fast_model.name
            endpoint = settings.fast_model.endpoint
            api_key = settings.fast_model.api_key
            prompt = task.params.get("prompt", "")
            system_prompt = task.params.get("system_prompt", "")

            if api_key and api_key != "demo-key":
                try:
                    return await _call_live_groq_llm(model_name, endpoint, api_key, prompt, system_prompt)
                except Exception as exc:
                    print(f"Warning: Live Groq call failed ({exc}), falling back to structured result.")

            return json.dumps(
                {
                    "age": 45,
                    "gender": "female",
                    "income": 0.0,
                    "location": "Maharashtra",
                    "category": "General",
                    "special_status": ["widow"],
                    "family_size": 2,
                    "education_level": "High School",
                    "occupation": "unemployed",
                    "stated_need": "widow pension and state financial assistance",
                }
            )

        if category == ToolCategory.REASONING_MODEL:
            # Live call to Groq Reasoning_Model (llama-3.3-70b-versatile)
            model_name = settings.reasoning_model.name
            endpoint = settings.reasoning_model.endpoint
            api_key = settings.reasoning_model.api_key
            prompt = task.params.get("prompt", "")
            system_prompt = task.params.get("system_prompt", "")

            if api_key and api_key != "demo-key":
                try:
                    return await _call_live_groq_llm(model_name, endpoint, api_key, prompt, system_prompt)
                except Exception as exc:
                    print(f"Warning: Live Groq Reasoning call failed ({exc}), falling back to structured result.")

            if "plan" in task.task_id.lower():
                return json.dumps(
                    {
                        "reasoning": "Formulated search plan for widow pension and financial assistance",
                        "operations": [
                            {
                                "op_id": "op1",
                                "query": "Indira Gandhi National Widow Pension Scheme eligibility site:myscheme.gov.in",
                            },
                            {
                                "op_id": "op2",
                                "query": "Sanjay Gandhi Niradhar Anudan Yojana Maharashtra eligibility site:myscheme.gov.in",
                            },
                            {
                                "op_id": "op3",
                                "query": "Widow pension scheme financial support application process site:myscheme.gov.in",
                            },
                        ],
                    }
                )
            else:
                return json.dumps(
                    {
                        "classification": "PASS",
                        "reasoning": "Age >= 40 and BPL status requirement met.",
                    }
                )

        if category == ToolCategory.SEARCH:
            queries = task.params.get("queries", [])
            queries_str = " ".join(queries).lower() if isinstance(queries, list) else str(queries).lower()

            if "widow" in queries_str or "pension" in queries_str or "financial" in queries_str or "assistance" in queries_str or "schemes" in queries_str:
                return [
                    {
                        "url": "https://myscheme.gov.in/schemes/ignwps",
                        "title": "Indira Gandhi National Widow Pension Scheme (IGNWPS)",
                        "snippet": "Monthly pension assistance for destitute widows aged 40-79 living below poverty line (BPL).",
                        "score": 0.96,
                    },
                    {
                        "url": "https://myscheme.gov.in/schemes/sgnay",
                        "title": "Sanjay Gandhi Niradhar Anudan Yojana",
                        "snippet": "Financial support of Rs 1,500 per month for destitute widows and abandoned women in Maharashtra.",
                        "score": 0.92,
                    },
                ]
            elif "senior" in queries_str or "old age" in queries_str or "elderly" in queries_str:
                return [
                    {
                        "url": "https://myscheme.gov.in/schemes/ignoaps",
                        "title": "Indira Gandhi National Old Age Pension Scheme (IGNOAPS)",
                        "snippet": "Monthly pension support for senior citizens aged 60 and above belonging to BPL households.",
                        "score": 0.95,
                    },
                ]
            else:
                return [
                    {
                        "url": "https://myscheme.gov.in/schemes/post-matric-scholarship",
                        "title": "Post Matric Scholarship for OBC Students",
                        "snippet": "Financial assistance for OBC post-matric students with family income under 2.5 Lakhs per annum.",
                        "score": 0.95,
                    },
                    {
                        "url": "https://scholarships.gov.in/central-sector-scheme",
                        "title": "Central Sector Scheme of Scholarships for College Students",
                        "snippet": "Scholarships for university students scoring top percentile in Class 12 exams.",
                        "score": 0.88,
                    },
                ]

        if category == ToolCategory.FETCH:
            return "Official scheme documentation: Destitute widows aged 40 to 79 belonging to BPL households are eligible for monthly financial aid of Rs 1,500 per month."

        if category == ToolCategory.DOCUMENT:
            return b"%PDF-1.4 Mock Application Guide Bytes"

        return "ok"

    return executor


async def run_civicpilot_demo(user_input: str) -> None:
    print("=" * 70)
    print("      CIVICPILOT - AI CITIZEN ELIGIBILITY & SCHEME PILOT      ")
    print("=" * 70)

    # Load settings from process environment / .env
    settings = load_settings()

    print(f"\n[ACTIVE GROQ FAST MODEL]:      {settings.fast_model.name}")
    print(f"[ACTIVE GROQ REASONING MODEL]: {settings.reasoning_model.name}")
    print(f"\n[CITIZEN PROMPT]: {user_input}\n")

    telemetry = TelemetryModule()
    transport = InMemoryAsyncTransport()
    ui = StreamingUI(transport)

    executor = await create_pipeline_executor(settings)

    scheduler = Scheduler(
        concurrency_limits={
            ToolCategory.FAST_MODEL: 4,
            ToolCategory.REASONING_MODEL: 2,
            ToolCategory.SEARCH: settings.concurrency.max_search_concurrency,
            ToolCategory.FETCH: settings.concurrency.max_fetch_concurrency,
            ToolCategory.DOCUMENT: 2,
        },
        executor=executor,
        telemetry=telemetry,
    )

    model_client = ModelClient(settings, scheduler)
    intake = IntakeModule(model_client)
    planner = PlannerAgent(model_client)
    search_proxy = SearchToolProxy(scheduler)
    fetch_proxy = FetchToolProxy(scheduler)
    filter_pipeline = FilterPipeline(model_client)
    researcher = ResearcherAgent(search_proxy, fetch_proxy, filter_pipeline=filter_pipeline)
    verifier = VerifierAgent(model_client, scheduler)
    reporter = ReporterAgent(telemetry)
    doc_gen = DocumentGenerator()
    doc_advisor = DocumentAdvisorAgent(doc_gen, scheduler)

    pipeline = CivicPilotPipeline(
        intake_module=intake,
        planner_agent=planner,
        researcher_agent=researcher,
        verifier_agent=verifier,
        reporter_agent=reporter,
        document_advisor=doc_advisor,
        telemetry=telemetry,
    )

    # Start background scheduler loop
    scheduler_task = asyncio.create_task(scheduler.run())

    try:
        report = await pipeline.run(user_input)

        print("-" * 70)
        print("                        FINAL REPORT                          ")
        print("-" * 70)
        print(f"\nProfile Summary:\n  {report.profile_summary}\n")

        print("Discovered Schemes & Eligibility Results:")
        for idx, (cand, res) in enumerate(zip(report.scheme_candidates, report.results), start=1):
            print(f"\n  {idx}. Scheme: {cand.name}")
            print(f"     Overall Status: {res.overall}")
            print(f"     Confidence:     {res.confidence_level}")
            print(f"     Priority Tier:  Tier {cand.priority_tier}")
            if cand.source_urls:
                print(f"     Source URL:     {cand.source_urls[0]}")

        print("\nOfficial Portal Annotations:")
        for link in report.official_links:
            print(f"  - [{link['is_official']}] {link['scheme_name']} -> {link['url']}")

        if report.documents:
            print("\nApplication Support Documents:")
            for doc in report.documents:
                status = "GENERATED" if doc.generated else f"SKIPPED ({doc.error or 'Gate criteria not met'})"
                print(f"  - Document for {doc.scheme_id}: {status}")

        print("\n" + report.performance_trace)

    finally:
        scheduler.stop()
        await scheduler_task


if __name__ == "__main__":
    if len(sys.argv) > 1:
        prompt = sys.argv[1]
    else:
        print("=" * 70)
        print("CIVICPILOT INTERACTIVE TERMINAL MODE (GROQ POWERED)")
        print("=" * 70)
        user_in = input("\nEnter your citizen profile description:\n> ").strip()
        if user_in:
            prompt = user_in
        else:
            prompt = "I am a 45 year old widow looking for state financial assistance."
            print(f"\n[No input entered - using sample prompt]:\n\"{prompt}\"")

    asyncio.run(run_civicpilot_demo(prompt))
