"""
eval/judge.py — EvalHarness: runs Sahayak against synthetic dementia scenarios.

Usage:
    harness = EvalHarness(cloud_llm, agent_graph, memory)
    results  = await harness.run_all(user_id="eval_user")
    report   = harness.generate_report(results)
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, BackgroundTasks, HTTPException

from eval.scenarios import DEMENTIA_SCENARIOS
from schemas import MemoryChunk, QueryRequest

logger = structlog.get_logger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Core harness
# ─────────────────────────────────────────────────────────────────────────────

class EvalHarness:
    """
    Orchestrates end-to-end evaluation of Sahayak against synthetic
    dementia scenarios.  Requires:
        cloud_llm   — exposes cloud_llm.judge(query, response, criteria) -> dict
        agent_graph — exposes agent_graph.arun(query, user_id) -> QueryResponse
        memory      — exposes memory.store(MemoryChunk) coroutine
    """

    def __init__(self, cloud_llm: Any, agent_graph: Any, memory: Any) -> None:
        self.cloud_llm = cloud_llm
        self.agent_graph = agent_graph
        self.memory = memory
        # job_id -> {"status": "running"|"done"|"error", "result": dict|None, "error": str|None}
        self._jobs: dict[str, dict] = {}

    # ──────────────────────────────────────────────────────────────────────
    # Single-scenario execution
    # ──────────────────────────────────────────────────────────────────────

    async def run_scenario(
        self, scenario: dict, user_id: str = "eval_user"
    ) -> dict:
        """
        Execute one scenario end-to-end:
            1. Plant seed memories.
            2. Run agent graph.
            3. String-match scoring.
            4. LLM-as-judge scoring.
            5. Combine into a final result dict.
        """
        scenario_id: str = scenario["id"]
        log = logger.bind(scenario_id=scenario_id, user_id=user_id)
        log.info("eval.scenario.start")

        # 1. Plant seed memories ─────────────────────────────────────────
        for seed in scenario.get("seed_memories", []):
            chunk = MemoryChunk(
                user_id=user_id,
                text=seed["text"],
                timestamp=datetime.now(tz=timezone.utc)
                - timedelta(hours=seed["timestamp_offset_hours"]),
                people=seed.get("people", []),
                tags=seed.get("tags", []),
            )
            try:
                await self.memory.store(chunk)
            except Exception as exc:
                log.warning("eval.seed.store_failed", error=str(exc), seed_text=seed["text"][:60])

        # 2. Run agent graph ─────────────────────────────────────────────
        t0 = time.monotonic()
        try:
            response = await self.agent_graph.arun(scenario["user_query"], user_id)
        except Exception as exc:
            log.error("eval.agent.failed", error=str(exc))
            latency_ms = (time.monotonic() - t0) * 1000
            return {
                "scenario_id": scenario_id,
                "category": scenario["category"],
                "difficulty": scenario["difficulty"],
                "score": 0.0,
                "latency_ms": latency_ms,
                "response": "",
                "routing": "error",
                "contains_score": 0.0,
                "hallucination_penalty": 0.0,
                "judge_score": 0.0,
                "judge_reasoning": f"Agent error: {exc}",
                "criteria_scores": {},
                "passed": False,
            }
        latency_ms = (time.monotonic() - t0) * 1000
        response_text: str = response.response
        log.info("eval.agent.done", latency_ms=round(latency_ms, 1))

        # 3. String checks ────────────────────────────────────────────────
        expected: list[str] = scenario.get("expected_answer_contains", [])
        forbidden: list[str] = scenario.get("forbidden_claims", [])

        contains_score: float = sum(
            1 for s in expected if s.lower() in response_text.lower()
        ) / max(len(expected), 1)

        hallucination_penalty: float = sum(
            0.3
            for s in forbidden
            if s.lower() in response_text.lower()
        )

        # 4. LLM judge ────────────────────────────────────────────────────
        try:
            judge_result: dict = await self.cloud_llm.judge(
                query=scenario["user_query"],
                response=response_text,
                criteria=scenario.get("judge_criteria", []),
            )
        except Exception as exc:
            log.warning("eval.judge.failed", error=str(exc))
            judge_result = {
                "score": 0.5,
                "reasoning": f"Judge unavailable: {exc}",
                "criteria_scores": {},
            }

        judge_score: float = float(judge_result.get("score", 0.0))

        # 5. Final score ──────────────────────────────────────────────────
        score: float = min(
            1.0,
            max(
                0.0,
                judge_score * 0.6 + contains_score * 0.4 - hallucination_penalty,
            ),
        )

        result = {
            "scenario_id": scenario_id,
            "category": scenario["category"],
            "difficulty": scenario["difficulty"],
            "score": round(score, 4),
            "latency_ms": round(latency_ms, 1),
            "response": response_text,
            "routing": getattr(response, "routing", "unknown"),
            "contains_score": round(contains_score, 4),
            "hallucination_penalty": round(hallucination_penalty, 4),
            "judge_score": round(judge_score, 4),
            "judge_reasoning": judge_result.get("reasoning", ""),
            "criteria_scores": judge_result.get("criteria_scores", {}),
            "passed": score >= 0.6,
        }
        log.info(
            "eval.scenario.done",
            score=result["score"],
            passed=result["passed"],
            hallucination_penalty=result["hallucination_penalty"],
        )
        return result

    # ──────────────────────────────────────────────────────────────────────
    # Full suite execution
    # ──────────────────────────────────────────────────────────────────────

    async def run_all(
        self,
        user_id: str = "eval_user",
        scenario_ids: list[str] | None = None,
    ) -> dict:
        """
        Run all (or a filtered subset of) scenarios with bounded concurrency.
        Returns an aggregate results dict.
        """
        scenarios = DEMENTIA_SCENARIOS
        if scenario_ids:
            id_set = set(scenario_ids)
            scenarios = [s for s in scenarios if s["id"] in id_set]

        if not scenarios:
            raise ValueError("No matching scenarios found.")

        # Limit concurrency to 3 to respect LLM rate limits.
        semaphore = asyncio.Semaphore(3)

        async def _run_with_sem(scenario: dict) -> dict:
            async with semaphore:
                return await self.run_scenario(scenario, user_id=user_id)

        logger.info("eval.run_all.start", total=len(scenarios), user_id=user_id)
        raw_results: list[dict] = await asyncio.gather(
            *[_run_with_sem(s) for s in scenarios],
            return_exceptions=False,
        )

        # Aggregate ──────────────────────────────────────────────────────
        scores = [r["score"] for r in raw_results]
        latencies = [r["latency_ms"] for r in raw_results]
        passed = [r for r in raw_results if r["passed"]]

        # By-category breakdown
        by_category: dict[str, list[float]] = {}
        for r in raw_results:
            by_category.setdefault(r["category"], []).append(r["score"])
        category_means = {
            cat: round(sum(vals) / len(vals), 4)
            for cat, vals in by_category.items()
        }

        # By-difficulty breakdown
        by_difficulty: dict[str, list[float]] = {}
        for r in raw_results:
            by_difficulty.setdefault(r["difficulty"], []).append(r["score"])
        difficulty_means = {
            diff: round(sum(vals) / len(vals), 4)
            for diff, vals in by_difficulty.items()
        }

        aggregate = {
            "mean_score": round(sum(scores) / len(scores), 4) if scores else 0.0,
            "pass_rate": round(len(passed) / len(raw_results), 4) if raw_results else 0.0,
            "mean_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0.0,
            "total_scenarios": len(raw_results),
            "total_passed": len(passed),
            "by_category": category_means,
            "by_difficulty": difficulty_means,
            "results": raw_results,
        }
        logger.info(
            "eval.run_all.done",
            mean_score=aggregate["mean_score"],
            pass_rate=aggregate["pass_rate"],
        )
        return aggregate

    # ──────────────────────────────────────────────────────────────────────
    # Report generation
    # ──────────────────────────────────────────────────────────────────────

    def generate_report(self, results: dict) -> str:
        """
        Produce a Markdown report from a run_all() results dict.
        """
        lines: list[str] = []

        lines.append("# Sahayak Evaluation Report")
        lines.append("")
        lines.append(
            f"Generated: {datetime.now(tz=timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"
        )
        lines.append("")

        # Summary table
        lines.append("## Summary")
        lines.append("")
        lines.append("| Metric | Value |")
        lines.append("|--------|-------|")
        lines.append(f"| Mean Score | {results['mean_score']:.4f} |")
        lines.append(f"| Pass Rate | {results['pass_rate']:.2%} |")
        lines.append(f"| Mean Latency | {results['mean_latency_ms']:.1f} ms |")
        lines.append(f"| Scenarios Run | {results['total_scenarios']} |")
        lines.append(f"| Passed | {results['total_passed']} |")
        lines.append("")

        # Per-category breakdown
        lines.append("## By Category")
        lines.append("")
        lines.append("| Category | Mean Score |")
        lines.append("|----------|-----------|")
        for cat, score in sorted(results["by_category"].items()):
            lines.append(f"| `{cat}` | {score:.4f} |")
        lines.append("")

        # Per-difficulty breakdown
        lines.append("## By Difficulty")
        lines.append("")
        lines.append("| Difficulty | Mean Score |")
        lines.append("|------------|-----------|")
        for diff in ["easy", "medium", "hard"]:
            score = results["by_difficulty"].get(diff, None)
            if score is not None:
                lines.append(f"| {diff.capitalize()} | {score:.4f} |")
        lines.append("")

        # Top 3 failures
        sorted_results = sorted(results["results"], key=lambda r: r["score"])
        failures = sorted_results[:3]
        lines.append("## Top 3 Failures (Lowest Score)")
        lines.append("")
        lines.append("| ID | Category | Difficulty | Score | Routing | Judge Reasoning |")
        lines.append("|----|----------|-----------|-------|---------|-----------------|")
        for r in failures:
            reasoning_short = (r["judge_reasoning"] or "")[:80].replace("|", "/")
            lines.append(
                f"| {r['scenario_id']} | {r['category']} | {r['difficulty']} "
                f"| {r['score']:.4f} | {r['routing']} | {reasoning_short} |"
            )
        lines.append("")

        # Top 3 successes
        successes = sorted(results["results"], key=lambda r: r["score"], reverse=True)[:3]
        lines.append("## Top 3 Successes (Highest Score)")
        lines.append("")
        lines.append("| ID | Category | Difficulty | Score | Routing |")
        lines.append("|----|----------|-----------|-------|---------|")
        for r in successes:
            lines.append(
                f"| {r['scenario_id']} | {r['category']} | {r['difficulty']} "
                f"| {r['score']:.4f} | {r['routing']} |"
            )
        lines.append("")

        # Hallucination summary
        halluc_scenarios = [
            r for r in results["results"] if r["hallucination_penalty"] > 0
        ]
        lines.append("## Hallucination Incidents")
        lines.append("")
        if halluc_scenarios:
            lines.append("| ID | Penalty | Response Excerpt |")
            lines.append("|----|---------|-----------------|")
            for r in halluc_scenarios:
                excerpt = (r["response"] or "")[:60].replace("|", "/")
                lines.append(
                    f"| {r['scenario_id']} | {r['hallucination_penalty']:.2f} | {excerpt}… |"
                )
        else:
            lines.append("No hallucination penalties were triggered. ✓")
        lines.append("")

        return "\n".join(lines)

    # ──────────────────────────────────────────────────────────────────────
    # Background job management
    # ──────────────────────────────────────────────────────────────────────

    async def run_job(
        self,
        job_id: str,
        user_id: str,
        scenario_ids: list[str] | None,
    ) -> None:
        """
        Wrapper used by the FastAPI router to run eval in the background.
        Stores result (or error) in self._jobs[job_id].
        """
        self._jobs[job_id]["status"] = "running"
        try:
            result = await self.run_all(user_id=user_id, scenario_ids=scenario_ids)
            self._jobs[job_id]["status"] = "done"
            self._jobs[job_id]["result"] = result
        except Exception as exc:
            logger.error("eval.job.failed", job_id=job_id, error=str(exc))
            self._jobs[job_id]["status"] = "error"
            self._jobs[job_id]["error"] = str(exc)

    def get_job(self, job_id: str) -> dict | None:
        """Return the job record for job_id, or None if not found."""
        return self._jobs.get(job_id)


# ─────────────────────────────────────────────────────────────────────────────
# FastAPI router
# ─────────────────────────────────────────────────────────────────────────────

eval_router = APIRouter(prefix="/eval", tags=["eval"])

# The harness instance is injected at app startup via eval_router.harness.
# Pattern: app.include_router(eval_router) then eval_router.harness = EvalHarness(...)

def _get_harness() -> EvalHarness:
    harness: EvalHarness | None = getattr(eval_router, "harness", None)
    if harness is None:
        raise HTTPException(
            status_code=503,
            detail="EvalHarness not initialised. Set eval_router.harness before using /eval endpoints.",
        )
    return harness


# ── POST /eval/run ────────────────────────────────────────────────────────────

class _RunRequest(dict):
    """Thin typed alias — parsed via FastAPI body."""


from pydantic import BaseModel


class RunRequest(BaseModel):
    user_id: str = "eval_user"
    scenario_ids: list[str] | None = None


@eval_router.post("/run", summary="Start an eval job")
async def start_eval(body: RunRequest, background_tasks: BackgroundTasks) -> dict:
    """
    Kick off an evaluation job in the background.
    Returns immediately with a job_id that can be polled via GET /eval/results/{job_id}.
    """
    harness = _get_harness()
    job_id = str(uuid.uuid4())
    harness._jobs[job_id] = {"status": "queued", "result": None, "error": None}

    background_tasks.add_task(
        harness.run_job,
        job_id=job_id,
        user_id=body.user_id,
        scenario_ids=body.scenario_ids,
    )
    logger.info("eval.job.queued", job_id=job_id, user_id=body.user_id)
    return {"job_id": job_id}


# ── GET /eval/results/{job_id} ────────────────────────────────────────────────

@eval_router.get("/results/{job_id}", summary="Poll eval job results")
async def get_results(job_id: str) -> dict:
    """
    Returns the full results dict when done, or {"status": "running"} / {"status": "queued"}
    while the job is still executing.  Raises 404 if the job_id is unknown.
    """
    harness = _get_harness()
    job = harness.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")

    if job["status"] in ("queued", "running"):
        return {"status": job["status"]}

    if job["status"] == "error":
        raise HTTPException(
            status_code=500,
            detail=f"Eval job failed: {job['error']}",
        )

    return job["result"]


# ── GET /eval/report/{job_id} ─────────────────────────────────────────────────

from fastapi.responses import PlainTextResponse


@eval_router.get(
    "/report/{job_id}",
    response_class=PlainTextResponse,
    summary="Get Markdown report for a completed eval job",
)
async def get_report(job_id: str) -> str:
    """
    Returns a human-readable Markdown report for a completed eval job.
    Returns 404 if unknown, 202 if still running, 500 if the job errored.
    """
    harness = _get_harness()
    job = harness.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")

    if job["status"] in ("queued", "running"):
        raise HTTPException(status_code=202, detail="Eval job still running.")

    if job["status"] == "error":
        raise HTTPException(
            status_code=500,
            detail=f"Eval job failed: {job['error']}",
        )

    return harness.generate_report(job["result"])


# ── GET /eval/scenarios ───────────────────────────────────────────────────────

@eval_router.get("/scenarios", summary="List all available eval scenarios (metadata only)")
async def list_scenarios() -> list[dict]:
    """
    Returns lightweight metadata for all 50 scenarios —
    id, category, difficulty.  Seed memories and answers are omitted.
    """
    return [
        {
            "id": s["id"],
            "category": s["category"],
            "difficulty": s["difficulty"],
            "user_query": s["user_query"],
        }
        for s in DEMENTIA_SCENARIOS
    ]
