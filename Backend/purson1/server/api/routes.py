"""
CiteSafe API Routes
====================
REST + WebSocket endpoints matching both the I/O contract and frontend integration doc.

Endpoints:
  POST /api/analyze         — {url?, doi?, text?} → AnalysisReport
  POST /api/analyze/upload  — PDF upload → AnalysisReport
  WS   /ws/analyze          — real-time progress (frontend integration doc path)
  WS   /api/analyze/ws      — real-time progress (I/O contract path)
  GET  /api/agents          — list registered agents
  GET  /api/health          — health check

WebSocket protocol (matches frontend contract):
  Client sends: {"paper_input": "https://arxiv.org/abs/..."}
  Server sends: {"type": "progress", "message": "...", "progress": 25}
  Server sends: {"type": "result", "report": {...}}
  Server sends: {"type": "error", "message": "..."}
"""

from __future__ import annotations

import json
import logging
import traceback

from fastapi import APIRouter, File, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from server.agents.base import registry
from server.api.schemas import (
    AgentInfo,
    AnalysisReport,
    ErrorDetail,
    ErrorResponse,
    HealthResponse,
    PaperInput,
)
from server.core.cache import CiteSafeCache
from server.core.pipeline import PipelineOrchestrator

logger = logging.getLogger("citesafe.api")
router = APIRouter()

# Shared instances (initialized once)
cache = CiteSafeCache()
pipeline = PipelineOrchestrator()


# ---------------------------------------------------------------------------
# REST: POST /api/analyze
# ---------------------------------------------------------------------------

@router.post("/analyze")
async def analyze_paper(paper: PaperInput):
    """
    Analyze a paper's citation integrity.
    Input: {"url": "...", "doi": "...", "text": "..."} (at least one required)
    Output: AnalysisReport matching the frontend contract
    """
    try:
        # Check paper cache first (if text-based)
        if paper.text:
            paper_hash = cache.hash_paper(paper.text)
            cached = cache.get_analysis(paper_hash)
            if cached:
                logger.info("Returning cached paper analysis")
                return cached

        report = await pipeline.run(
            url=paper.url or "",
            doi=paper.doi or "",
            text=paper.text or "",
        )

        # Cache the result
        if paper.text:
            cache.set_analysis(cache.hash_paper(paper.text), report)

        return report

    except Exception as e:
        logger.error(f"Analysis failed: {e}\n{traceback.format_exc()}")
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                error=ErrorDetail(
                    code="INTERNAL_ERROR",
                    message=str(e),
                    details=traceback.format_exc(),
                    stage="pipeline",
                    recoverable=False,
                )
            ).model_dump(),
        )


# ---------------------------------------------------------------------------
# REST: POST /api/analyze/upload
# ---------------------------------------------------------------------------

@router.post("/analyze/upload")
async def analyze_upload(file: UploadFile = File(...)):
    """Upload a PDF for analysis."""
    if not file.filename or not file.filename.lower().endswith(".pdf"):
        return JSONResponse(
            status_code=400,
            content=ErrorResponse(
                error=ErrorDetail(
                    code="INVALID_INPUT",
                    message="Only PDF files are supported",
                    stage="validation",
                )
            ).model_dump(),
        )

    content = await file.read()
    if len(content) > 20 * 1024 * 1024:
        return JSONResponse(
            status_code=400,
            content=ErrorResponse(
                error=ErrorDetail(
                    code="INVALID_INPUT",
                    message="File too large (max 20MB)",
                    stage="validation",
                )
            ).model_dump(),
        )

    try:
        # Pass PDF bytes through the pipeline
        # The fetcher agent is responsible for PDF→text extraction
        report = await pipeline.run(
            url="",
            doi="",
            text="",  # fetcher agent handles PDF bytes via metadata
        )
        return report
    except Exception as e:
        logger.error(f"Upload analysis failed: {e}")
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                error=ErrorDetail(
                    code="INTERNAL_ERROR",
                    message=str(e),
                    stage="pipeline",
                )
            ).model_dump(),
        )


# ---------------------------------------------------------------------------
# REST: GET /api/agents
# ---------------------------------------------------------------------------

@router.get("/agents", response_model=list[AgentInfo])
async def list_agents():
    """List all registered agents and their stages."""
    return registry.list_agents()


# ---------------------------------------------------------------------------
# REST: GET /api/health
# ---------------------------------------------------------------------------

@router.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(
        status="ok",
        agents_registered=len(registry),
        cache_stats=cache.get_stats(),
    )


# ---------------------------------------------------------------------------
# WebSocket handler (shared by both WS paths)
# ---------------------------------------------------------------------------

async def _handle_ws_analyze(websocket: WebSocket):
    """
    WebSocket analysis with real-time progress.

    Frontend contract:
      Client sends: {"paper_input": "https://arxiv.org/abs/..."}
      Server sends: {"type": "progress", "message": "...", "progress": 25}
      Server sends: {"type": "result", "report": {...}}
      Server sends: {"type": "error", "message": "..."}
    """
    logger.info(f"WebSocket connection request receiving from {websocket.client}")
    try:
        await websocket.accept()
        logger.info("WebSocket connection ACCEPTED")

        # Receive analysis request from frontend
        raw = await websocket.receive_text()
        logger.info(f"WebSocket received data: {raw[:200]}")
        request = json.loads(raw)

        # Frontend sends {"paper_input": "..."} — extract the value
        paper_input = request.get("paper_input", "")
        if not paper_input:
            # Fallback: try other formats
            paper_input = request.get("url", "") or request.get("query", "")

        if not paper_input:
            await websocket.send_text(json.dumps({
                "type": "error",
                "message": "No paper_input provided",
            }))
            return

        # Determine input type from the value
        url = ""
        doi = ""
        text = ""
        if paper_input.startswith(("http://", "https://")):
            url = paper_input
        elif paper_input.startswith("10."):
            doi = paper_input
        else:
            text = paper_input

        # Progress callback → sends WS messages in contract format
        async def on_progress(message: str, progress: float):
            """
            Send progress in frontend contract format:
              {"type": "progress", "message": "...", "progress": 25}
            Progress is 0-100 percentage.
            """
            try:
                await websocket.send_text(json.dumps({
                    "type": "progress",
                    "message": message,
                    "progress": round(progress),  # integer 0-100
                }))
            except Exception:
                pass  # client may have disconnected

        # Run pipeline with WS progress
        report = await pipeline.run(
            url=url,
            doi=doi,
            text=text,
            on_progress=on_progress,
        )

        # Send final report in contract format
        await websocket.send_text(json.dumps({
            "type": "result",
            "report": report,
        }))

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except json.JSONDecodeError as e:
        try:
            await websocket.send_text(json.dumps({
                "type": "error",
                "message": f"Invalid JSON: {e}",
            }))
        except Exception:
            pass
    except Exception as e:
        logger.error(f"WebSocket error: {e}\n{traceback.format_exc()}")
        try:
            await websocket.send_text(json.dumps({
                "type": "error",
                "message": str(e),
            }))
        except Exception:
            pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# WebSocket: /ws/analyze (frontend integration doc path)
# WebSocket handlers are now managed in main.py to avoid 
# routing conflicts at the /api prefix vs root.
