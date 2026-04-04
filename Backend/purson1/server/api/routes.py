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
        # Extract text from PDF bytes before passing to pipeline
        from server.utils.pdf import PDFScraper
        try:
            extracted_text = PDFScraper.extract_text_from_bytes(content)
        except Exception as pdf_err:
            return JSONResponse(
                status_code=400,
                content=ErrorResponse(
                    error=ErrorDetail(
                        code="PDF_PARSE_FAILED",
                        message=f"Could not extract text from PDF: {pdf_err}",
                        stage="validation",
                    )
                ).model_dump(),
            )

        report = await pipeline.run(
            url="",
            doi="",
            text=extracted_text,
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
# REST: Manual citation override (stored in SQLite)
# ---------------------------------------------------------------------------

import aiosqlite
import os

OVERRIDES_DB = os.path.join(os.path.dirname(os.path.dirname(__file__)), "overrides.db")

async def _init_overrides_db():
    async with aiosqlite.connect(OVERRIDES_DB) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS overrides (
                paper_key TEXT NOT NULL,
                citation_id INTEGER NOT NULL,
                verdict TEXT NOT NULL,
                notes TEXT DEFAULT '',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (paper_key, citation_id)
            )
        """)
        await db.commit()


@router.post("/override")
async def set_override(body: dict):
    """Save a manual verdict override for a citation.
    Body: {paper_key, citation_id, verdict, notes?}
    """
    await _init_overrides_db()
    paper_key = body.get("paper_key", "")
    cid = body.get("citation_id", 0)
    verdict = body.get("verdict", "")
    notes = body.get("notes", "")

    if not paper_key or not cid or verdict not in ("supported", "contradicted", "uncertain", "not_found"):
        return JSONResponse(status_code=400, content={"error": "Invalid override data"})

    async with aiosqlite.connect(OVERRIDES_DB) as db:
        await db.execute(
            "INSERT OR REPLACE INTO overrides (paper_key, citation_id, verdict, notes) VALUES (?, ?, ?, ?)",
            (paper_key, cid, verdict, notes),
        )
        await db.commit()

    return {"status": "ok", "paper_key": paper_key, "citation_id": cid, "verdict": verdict}


@router.get("/overrides/{paper_key}")
async def get_overrides(paper_key: str):
    """Get all manual overrides for a paper."""
    await _init_overrides_db()
    async with aiosqlite.connect(OVERRIDES_DB) as db:
        async with db.execute(
            "SELECT citation_id, verdict, notes FROM overrides WHERE paper_key = ?",
            (paper_key,),
        ) as cursor:
            rows = await cursor.fetchall()
    return {str(r[0]): {"verdict": r[1], "notes": r[2]} for r in rows}


@router.delete("/override")
async def delete_override(body: dict):
    """Remove a manual override."""
    await _init_overrides_db()
    paper_key = body.get("paper_key", "")
    cid = body.get("citation_id", 0)
    async with aiosqlite.connect(OVERRIDES_DB) as db:
        await db.execute(
            "DELETE FROM overrides WHERE paper_key = ? AND citation_id = ?",
            (paper_key, cid),
        )
        await db.commit()
    return {"status": "ok"}


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
    await websocket.accept()
    logger.info("WebSocket client connected")

    try:
        # Receive analysis request from frontend
        raw = await websocket.receive_text()
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
# ---------------------------------------------------------------------------

@router.websocket("/ws/analyze")
async def ws_analyze(websocket: WebSocket):
    await _handle_ws_analyze(websocket)


# ---------------------------------------------------------------------------
# WebSocket: /api/analyze/ws (I/O contract path)
# ---------------------------------------------------------------------------

@router.websocket("/analyze/ws")
async def ws_analyze_alt(websocket: WebSocket):
    await _handle_ws_analyze(websocket)
