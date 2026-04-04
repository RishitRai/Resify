"""
CiteSafe Pipeline Orchestrator
===============================
Executes the analysis state machine:
  FETCHING → EXTRACTING → CHECKING_EXISTENCE → EMBEDDING_GATE
    → LLM_VERIFICATION (only if needed) → SYNTHESIZING → COMPLETE

Key behaviors:
  - Runs agents registered for each stage
  - One agent failing doesn't kill the pipeline (error isolation)
  - Circuit breaker disables repeatedly-failing agents
  - Progress updates sent in frontend contract format:
      {"type": "progress", "message": "...", "progress": 25}
  - Final report matches the contract exactly
  - Token budget enforced (LLM agents skipped when exhausted)
"""

from __future__ import annotations

import asyncio
import logging
import time
import traceback
from typing import Any, Callable, Coroutine, Optional

from server.agents.base import (
    AgentResult,
    BaseAgent,
    PipelineContext,
    PipelineStage,
    TokenBudget,
    STAGE_ORDER,
    registry,
)

logger = logging.getLogger("citesafe.pipeline")


# Stage → (progress_start%, progress_end%, default_message)
STAGE_PROGRESS = {
    PipelineStage.FETCHING:            (5,  15, "Fetching paper..."),
    PipelineStage.EXTRACTING:          (15, 25, "Extracting citations..."),
    PipelineStage.CHECKING_EXISTENCE:  (25, 45, "Checking citation existence..."),
    PipelineStage.EMBEDDING_GATE:      (45, 65, "Verifying accuracy..."),
    PipelineStage.LLM_VERIFICATION:    (65, 85, "Deep verification..."),
    PipelineStage.SYNTHESIZING:        (85, 95, "Generating report..."),
}


class PipelineOrchestrator:
    """
    Executes the registered agent pipeline against a given input.

    Usage:
        orchestrator = PipelineOrchestrator()
        report = await orchestrator.run(
            url="https://arxiv.org/abs/2301.00001",
            on_progress=my_ws_callback,
        )
    """

    def __init__(self, agent_registry=None):
        self.registry = agent_registry or registry

    async def run(
        self,
        url: str = "",
        doi: str = "",
        text: str = "",
        token_limit: int = 100_000,
        on_progress: Optional[Callable] = None,
    ) -> dict:
        """
        Execute the full analysis pipeline.

        Args:
            url:          Paper URL (arxiv, etc.)
            doi:          Paper DOI
            text:         Raw paper text
            token_limit:  Max LLM tokens for this run
            on_progress:  Async callback(message: str, progress: float) for WS updates

        Returns:
            Final report dict matching the frontend contract
        """
        pipeline_start = time.perf_counter()

        # Build context
        ctx = PipelineContext(
            paper_url=url,
            paper_doi=doi,
            paper_text=text,
            token_budget=TokenBudget(limit=token_limit),
            _progress_callback=on_progress,
        )

        # Run each stage in order
        for stage in STAGE_ORDER:
            agents = self.registry.get_agents_for_stage(stage)
            if not agents:
                # No agent for this stage — skip it
                # (normal during development when not all agents are built yet)
                logger.debug(f"No agents for stage {stage.value}, skipping")
                continue

            # Check if we should skip LLM stage (embedding gate resolved everything)
            if stage == PipelineStage.LLM_VERIFICATION and not ctx.embedding_needs_llm:
                logger.info("No citations need LLM — skipping LLM_VERIFICATION")
                continue

            # Update stage
            ctx.stage = stage
            progress_start, progress_end, default_msg = STAGE_PROGRESS[stage]
            await ctx.send_progress(default_msg, progress_start)

            # Run all agents registered for this stage CONCURRENTLY
            tasks = [self._run_agent(agent, ctx) for agent in agents]
            results = await asyncio.gather(*tasks)
            
            # Apply results securely
            for agent, result in zip(agents, results):
                if result:
                    self._apply_result(agent, result, ctx)

            # Progress at end of stage
            await ctx.send_progress(f"Completed {stage.value}", progress_end)

        # Build final report
        ctx.stage = PipelineStage.SYNTHESIZING
        report = self._build_report(ctx)
        ctx.report = report

        # Record total latency
        total_ms = (time.perf_counter() - pipeline_start) * 1000
        ctx.stats["latency_total_ms"] = round(total_ms, 1)
        report["stats"]["latency_ms"] = round(total_ms, 1)

        ctx.stage = PipelineStage.COMPLETE
        await ctx.send_progress("Analysis complete!", 100)

        return report

    async def _run_agent(self, agent: BaseAgent, ctx: PipelineContext) -> Optional[AgentResult]:
        """Run a single agent with error isolation, timing, and circuit breaking."""
        name = agent.name

        # Circuit breaker check
        if self.registry.circuit_breaker.is_open(name):
            logger.warning(f"[{name}] skipped — circuit breaker open")
            return None

        # Pre-check (budget guard, custom logic)
        try:
            if not await agent.pre_check(ctx):
                logger.info(f"[{name}] skipped by pre_check")
                return None
        except Exception as e:
            logger.error(f"[{name}] pre_check failed: {e}")
            return None

        # Execute
        t0 = time.perf_counter()
        try:
            from server.config import settings
            result = await asyncio.wait_for(
                agent.process(ctx),
                timeout=settings.AGENT_TIMEOUT_SECONDS,
            )
            elapsed_ms = (time.perf_counter() - t0) * 1000
            result.latency_ms = round(elapsed_ms, 1)
            ctx.agent_timings[name] = round(elapsed_ms, 1)

            self.registry.circuit_breaker.record_success(name)
            logger.info(f"[{name}] completed in {elapsed_ms:.0f}ms")
            return result

        except asyncio.TimeoutError:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self.registry.circuit_breaker.record_failure(name)
            await ctx.send_error(
                code="AGENT_TIMEOUT",
                message=f"Agent {name} timed out",
                details=f"Exceeded 120s timeout after {elapsed_ms:.0f}ms",
                stage=ctx.stage.value,
                recoverable=True,
            )
            logger.error(f"[{name}] timed out after {elapsed_ms:.0f}ms")
            return None

        except Exception as e:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            self.registry.circuit_breaker.record_failure(name)
            await ctx.send_error(
                code=_error_code_for_stage(ctx.stage),
                message=f"Agent {name} failed: {str(e)}",
                details=traceback.format_exc(),
                stage=ctx.stage.value,
                recoverable=True,
            )
            logger.error(f"[{name}] failed: {e}")
            return None

    def _apply_result(self, agent: BaseAgent, result: AgentResult, ctx: PipelineContext):
        """
        Store an agent's result into the pipeline context.
        Maps agent output to the correct context fields based on stage.
        Also accumulates stats.
        """
        # Accumulate tokens
        if result.tokens_used > 0:
            ctx.token_budget.consume(agent.name, result.tokens_used)
            ctx.stats["total_tokens"] += result.tokens_used

        # Store based on stage
        if result.status != "success":
            return

        data = result.data
        stage = agent.stage

        if stage == PipelineStage.FETCHING:
            ctx.fetcher_result = data
            # If fetcher returned text, store it
            if data.get("text"):
                ctx.paper_text = data["text"]
            ctx.stats["latency_fetch_ms"] = result.latency_ms

        elif stage == PipelineStage.EXTRACTING:
            ctx.citations = data.get("citations", [])
            ctx.stats["citations_total"] = len(ctx.citations)
            ctx.stats["latency_extract_ms"] = result.latency_ms

        elif stage == PipelineStage.CHECKING_EXISTENCE:
            # Existence agent may return results for multiple citations
            # or for a single citation — handle both
            if "results" in data:
                for cid_str, res in data["results"].items():
                    ctx.existence_results[int(cid_str)] = res
            elif "status" in data:
                # Single-citation result — need citation_id from somewhere
                cid = data.get("citation_id", 0)
                ctx.existence_results[cid] = data
            ctx.stats["latency_existence_ms"] = max(
                ctx.stats["latency_existence_ms"], result.latency_ms
            )
            # Update found/not_found counts
            found = sum(1 for r in ctx.existence_results.values() if r.get("status") == "found")
            ctx.stats["citations_found"] = found
            ctx.stats["citations_not_found"] = ctx.stats["citations_total"] - found

        elif stage == PipelineStage.EMBEDDING_GATE:
            ctx.embedding_resolved = data.get("resolved", [])
            ctx.embedding_needs_llm = data.get("needs_llm", [])
            ctx.stats["citations_resolved_embedding"] = len(ctx.embedding_resolved)
            ctx.stats["citations_sent_to_llm"] = len(ctx.embedding_needs_llm)
            ctx.stats["latency_embedding_ms"] = result.latency_ms

        elif stage == PipelineStage.LLM_VERIFICATION:
            if isinstance(data, list):
                ctx.llm_results = data
            elif "results" in data:
                ctx.llm_results = data["results"]
            else:
                ctx.llm_results = [data]
            ctx.stats["latency_llm_ms"] = result.latency_ms

        elif stage == PipelineStage.SYNTHESIZING:
            # Agent built the report itself
            ctx.report = data

    def _build_report(self, ctx: PipelineContext) -> dict:
        """
        Build the final report in the exact frontend contract format.

        Output shape:
        {
            "integrity_score": 78,
            "total_citations": 30,
            "summary": {"supported": 22, "contradicted": 1, "uncertain": 2, "not_found": 3, "metadata_errors": 0},
            "paper": {"title", "authors", "year", "source"},
            "citations": [{id, claim, reference, source_found, existence_status, metadata_status, verification}],
            "stats": {"total_tokens", "total_api_calls", "cache_hits", "latency_ms", "estimated_cost"}
        }
        """
        # If a synthesizer agent already built the report, use it
        if ctx.report:
            return ctx.report

        # Merge all verification results into final citation list
        final_citations = self._merge_citation_results(ctx)

        # Count verdicts
        supported = 0
        contradicted = 0
        uncertain = 0
        not_found = 0
        metadata_errors = 0

        for c in final_citations:
            existence = c.get("existence_status", "not_found")
            if existence == "not_found":
                not_found += 1
                continue

            verification = c.get("verification")
            if verification:
                verdict = verification.get("verdict", "uncertain")
                if verdict == "supported":
                    supported += 1
                elif verdict == "contradicted":
                    contradicted += 1
                else:
                    uncertain += 1

            meta_status = c.get("metadata_status")
            if meta_status and meta_status not in ("correct", None):
                metadata_errors += 1

        total = len(final_citations)

        # Integrity score: % supported out of those that were found
        found_total = total - not_found
        integrity_score = round(
            (supported / found_total * 100) if found_total > 0 else 0, 1
        )

        # Paper metadata
        paper_meta = {}
        if ctx.fetcher_result:
            paper_meta = {
                "title": ctx.fetcher_result.get("title", ""),
                "authors": ctx.fetcher_result.get("authors", []),
                "year": ctx.fetcher_result.get("year"),
                "source": ctx.fetcher_result.get("source", ""),
            }

        # Estimated cost (rough: $0.15 per 1M input tokens for Gemini Flash)
        estimated_cost = round(ctx.stats["total_tokens"] * 0.00000015, 4)

        return {
            "integrity_score": integrity_score,
            "total_citations": total,
            "summary": {
                "supported": supported,
                "contradicted": contradicted,
                "uncertain": uncertain,
                "not_found": not_found,
                "metadata_errors": metadata_errors,
            },
            "paper": paper_meta,
            "citations": final_citations,
            "stats": {
                "total_tokens": ctx.stats["total_tokens"],
                "total_api_calls": ctx.stats.get("total_api_calls", 0),
                "cache_hits": ctx.stats.get("cache_hits", 0),
                "latency_ms": ctx.stats.get("latency_total_ms", 0),
                "estimated_cost": estimated_cost,
                "processing_time_ms": ctx.stats.get("latency_total_ms", 0),
                "agents_invoked": len(ctx.agent_timings),
            },
        }

    def _merge_citation_results(self, ctx: PipelineContext) -> list[dict]:
        """
        Merge results from all stages into the per-citation format the frontend expects:
        {
            "id": 1,
            "claim": "...",
            "reference": {"authors", "title", "year", "venue"},
            "source_found": {"title", "authors", "year"} | null,
            "existence_status": "found" | "not_found",
            "metadata_status": "correct" | null,
            "verification": {"verdict", "confidence", "evidence", "method"} | null
        }
        """
        merged = []

        for cit in ctx.citations:
            cid = cit.get("id", 0)
            entry = {
                "id": cid,
                "claim": cit.get("claim", ""),
                "context": cit.get("context", ""),
                "reference": cit.get("reference", {}),
                "source_found": None,
                "existence_status": "not_found",
                "metadata_status": None,
                "verification": None,
            }

            # Existence result
            existence = ctx.existence_results.get(cid)
            if existence:
                entry["existence_status"] = existence.get("status", "not_found")
                if existence.get("status") == "found" and existence.get("paper"):
                    source = existence["paper"]
                    entry["source_found"] = {
                        "title": source.get("title", ""),
                        "authors": source.get("authors", []),
                        "year": source.get("year"),
                    }

            # Skip verification for not-found citations
            if entry["existence_status"] == "not_found":
                merged.append(entry)
                continue

            # Check embedding resolved
            emb_match = next(
                (r for r in ctx.embedding_resolved if r.get("citation", {}).get("id") == cid),
                None,
            )
            if emb_match:
                entry["verification"] = {
                    "verdict": emb_match.get("verdict", "uncertain"),
                    "confidence": emb_match.get("confidence", 0),
                    "evidence": emb_match.get("evidence"),
                    "method": emb_match.get("method", "embedding"),
                }
                merged.append(entry)
                continue

            # Check LLM results
            llm_match = next(
                (r for r in ctx.llm_results if r.get("citation", {}).get("id") == cid),
                None,
            )
            if llm_match:
                entry["verification"] = {
                    "verdict": llm_match.get("verdict", "uncertain"),
                    "confidence": llm_match.get("confidence", 0),
                    "evidence": llm_match.get("evidence"),
                    "method": llm_match.get("method", "llm"),
                }
                merged.append(entry)
                continue

            # No verification result — mark as uncertain
            if entry["existence_status"] == "found":
                entry["verification"] = {
                    "verdict": "uncertain",
                    "confidence": 0,
                    "evidence": None,
                    "method": "none",
                }

            merged.append(entry)

        return merged


def _error_code_for_stage(stage: PipelineStage) -> str:
    """Map pipeline stage to the contract's error codes."""
    return {
        PipelineStage.FETCHING: "FETCH_FAILED",
        PipelineStage.EXTRACTING: "EXTRACT_FAILED",
        PipelineStage.CHECKING_EXISTENCE: "EXISTENCE_RATE_LIMITED",
        PipelineStage.EMBEDDING_GATE: "VERIFICATION_FAILED",
        PipelineStage.LLM_VERIFICATION: "VERIFICATION_FAILED",
        PipelineStage.SYNTHESIZING: "INTERNAL_ERROR",
    }.get(stage, "INTERNAL_ERROR")
