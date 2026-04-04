"""
Resify Backend Retrieval Diagnostics
=====================================
Tests each agent in the real pipeline to identify retrieval failures.

Run:
    python tests/test_retrieval_diagnosis.py
"""

import asyncio
import json
import sys
import os
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# ── Helpers ──────────────────────────────────────────────────────────────────

PASS = 0
FAIL = 0

def ok(msg):
    global PASS
    PASS += 1
    print(f"  [PASS] {msg}")

def fail(msg, detail=""):
    global FAIL
    FAIL += 1
    print(f"  [FAIL] {msg}")
    if detail:
        print(f"         {detail}")

def section(title):
    print(f"\n{'='*60}\n  {title}\n{'='*60}")

# ── Test 1: Agent Registration Order ─────────────────────────────────────────

async def test_agent_registration():
    """Check which agents are actually registered (real vs dummy)."""
    section("1. Agent Registration (real vs dummy)")

    from server.agents.base import registry

    agents = registry.list_agents()
    print(f"  Registered agents: {len(agents)}")

    for a in agents:
        name = a["name"]
        desc = a["description"]
        is_dummy = "dummy" in desc.lower()
        tag = "DUMMY" if is_dummy else "REAL"
        print(f"    [{a['stage']}] {name} ({tag}) - {desc}")

    # Check which stages have real agents
    real_stages = set()
    dummy_stages = set()
    for a in agents:
        if "dummy" in a["description"].lower():
            dummy_stages.add(a["stage"])
        else:
            real_stages.add(a["stage"])

    if "fetching" in real_stages:
        ok("Real fetcher agent registered")
    else:
        fail("Fetcher is still dummy", "Real fetcher.py exists but isn't being loaded")

    if "extracting" in real_stages:
        ok("Real extractor agent registered")
    else:
        fail("Extractor is still dummy")

    if "checking_existence" in real_stages:
        ok("Real existence agent registered")
    else:
        fail("Existence is still dummy")

    # These are expected to be dummy for now
    for stage in ("embedding_gate", "llm_verification"):
        if stage in dummy_stages:
            print(f"  [INFO] {stage} is still dummy (expected - not yet built as pipeline agent)")


# ── Test 2: Semantic Scholar API ─────────────────────────────────────────────

async def test_semantic_scholar_api():
    """Test that Semantic Scholar search actually returns results."""
    section("2. Semantic Scholar API")

    from server.utils.apis import SemanticScholarAPI, APIClientManager

    api = SemanticScholarAPI()

    # Test 1: Search by title
    query = "Attention Is All You Need Vaswani 2017"
    try:
        results = await api.search(query, limit=3)
        if results and len(results) > 0:
            ok(f"Search returned {len(results)} results for '{query[:40]}...'")
            top = results[0]
            print(f"    Top result: {top.get('title', 'N/A')}")
            print(f"    Year: {top.get('year')}, Authors: {len(top.get('authors', []))}")
            if top.get("abstract"):
                ok("Abstract returned (needed for embedding verification)")
            else:
                fail("No abstract in search results", "Embedding gate needs abstract to verify claims")
        else:
            fail(f"Search returned 0 results for known paper", f"Query: {query}")
    except Exception as e:
        fail(f"Search threw exception: {e}")

    # Test 2: Direct paper lookup
    try:
        paper = await api.get_paper("ARXIV:1706.03762")
        if paper:
            ok(f"Direct lookup found paper: {paper.get('title', 'N/A')[:50]}")
        else:
            fail("Direct lookup returned None for ARXIV:1706.03762")
    except Exception as e:
        fail(f"Direct lookup threw: {e}")

    # Test 3: Check if S2_API_KEY is configured
    from server.config import settings
    if settings.S2_API_KEY:
        ok(f"S2_API_KEY is configured (higher rate limits)")
    else:
        print("  [WARN] S2_API_KEY not set - using unauthenticated rate limits (100 req/5min)")
        print("         Set S2_API_KEY in .env for production use")


# ── Test 3: ArXiv API ───────────────────────────────────────────────────────

async def test_arxiv_api():
    """Test ArXiv paper fetching."""
    section("3. ArXiv API")

    from server.utils.apis import ArXivAPI

    api = ArXivAPI()

    try:
        paper = await api.get_paper("1706.03762")
        if paper:
            ok(f"ArXiv returned: {paper.get('title', 'N/A')[:50]}")
            if paper.get("abstract"):
                ok("Abstract present")
            else:
                fail("No abstract from ArXiv")
            if paper.get("pdf_url"):
                ok(f"PDF URL: {paper['pdf_url']}")
            else:
                fail("No PDF URL from ArXiv")
            if paper.get("authors"):
                ok(f"Authors: {len(paper['authors'])} found")
            else:
                fail("No authors from ArXiv")
        else:
            fail("ArXiv returned None for 1706.03762")
    except Exception as e:
        fail(f"ArXiv threw: {e}")


# ── Test 4: Real Fetcher Agent ──────────────────────────────────────────────

async def test_fetcher_agent():
    """Test the real fetcher agent end-to-end."""
    section("4. Real Fetcher Agent")

    from server.agents.base import PipelineContext, TokenBudget
    from server.agents.fetcher import FetcherAgent

    agent = FetcherAgent()

    # Test with arxiv URL
    ctx = PipelineContext(
        paper_url="https://arxiv.org/abs/1706.03762",
        token_budget=TokenBudget(limit=100000),
    )

    try:
        result = await agent.process(ctx)
        if result.status == "success":
            ok("Fetcher succeeded")
            data = result.data
            if data.get("title") and data["title"] != "Document Title":
                ok(f"Title: {data['title'][:50]}")
            else:
                fail("Title is fallback/missing", f"Got: {data.get('title')}")

            if data.get("abstract"):
                ok(f"Abstract: {len(data['abstract'])} chars")
            else:
                fail("No abstract returned", "Downstream agents need this for verification")

            if data.get("text"):
                ok(f"Full text: {len(data['text'])} chars")
            else:
                print("  [WARN] No full text (PDF extraction may have failed)")
                print("         Extraction will rely on abstract only")

            if data.get("authors") and len(data["authors"]) > 0:
                ok(f"Authors: {data['authors'][:3]}")
            else:
                fail("No authors returned")
        else:
            fail(f"Fetcher returned error: {result.error}")
    except Exception as e:
        fail(f"Fetcher threw: {e}")
        traceback.print_exc()


# ── Test 5: Extractor Agent ─────────────────────────────────────────────────

async def test_extractor_agent():
    """Test the extractor agent (requires GEMINI_API_KEY)."""
    section("5. Real Extractor Agent")

    from server.agents.extractor import ExtractorAgent

    if not os.environ.get("GEMINI_API_KEY") and not os.environ.get("GOOGLE_API_KEY"):
        fail("GEMINI_API_KEY not set - extractor cannot work")
        print("         Set GEMINI_API_KEY in .env or environment")
        print("         This is WHY extraction returns 0 citations!")
        return

    agent = ExtractorAgent()
    if not agent.client:
        fail("GenAI client failed to initialize even with API key set")
        return

    ok("GenAI client initialized")

    from server.agents.base import PipelineContext, TokenBudget

    sample_text = """
    Attention mechanisms have become fundamental in neural networks.
    The Transformer architecture (Vaswani et al., 2017) introduced self-attention
    as a complete replacement for recurrence. BERT (Devlin et al., 2019) showed
    that bidirectional pre-training leads to improvements on downstream tasks.
    GPT-2 (Radford et al., 2019) demonstrated that language models can generate
    coherent long-form text.
    """

    ctx = PipelineContext(
        paper_text=sample_text,
        token_budget=TokenBudget(limit=100000),
    )

    try:
        result = await agent.process(ctx)
        if result.status == "success":
            citations = result.data.get("citations", [])
            if len(citations) > 0:
                ok(f"Extracted {len(citations)} citations")
                for c in citations[:3]:
                    ref = c.get("reference", {})
                    print(f"    #{c['id']}: {ref.get('authors', '?')} ({ref.get('year', '?')}) - {c['claim'][:60]}")
            else:
                fail("Extracted 0 citations from sample text")
        else:
            fail(f"Extractor returned error: {result.error}")
    except Exception as e:
        fail(f"Extractor threw: {e}")
        traceback.print_exc()


# ── Test 6: Existence Agent ─────────────────────────────────────────────────

async def test_existence_agent():
    """Test the existence checker with known citations."""
    section("6. Real Existence Agent")

    from server.agents.base import PipelineContext, TokenBudget
    from server.agents.existence import ExistenceAgent

    agent = ExistenceAgent()

    citations = [
        {
            "id": 1,
            "claim": "Introduced the Transformer architecture with self-attention",
            "reference": {
                "authors": "Vaswani et al.",
                "title": "Attention Is All You Need",
                "year": 2017,
                "venue": "NeurIPS"
            }
        },
        {
            "id": 2,
            "claim": "Showed bidirectional pre-training improves downstream tasks",
            "reference": {
                "authors": "Devlin et al.",
                "title": "BERT: Pre-training of Deep Bidirectional Transformers",
                "year": 2019,
                "venue": "NAACL"
            }
        },
        {
            "id": 3,
            "claim": "This is a completely fabricated paper that does not exist",
            "reference": {
                "authors": "Fakerson et al.",
                "title": "A Completely Made Up Paper Title That Cannot Exist",
                "year": 2099,
                "venue": "Fake Conference"
            }
        }
    ]

    ctx = PipelineContext(
        token_budget=TokenBudget(limit=100000),
    )
    ctx.citations = citations

    try:
        result = await agent.process(ctx)
        if result.status == "success":
            results = result.data.get("results", {})

            # Check "Attention Is All You Need"
            r1 = results.get("1", {})
            if r1.get("status") == "found":
                ok(f"Found 'Attention Is All You Need' (score: {r1.get('match_score', '?')})")
                paper = r1.get("paper", {})
                if paper.get("abstract"):
                    ok(f"  Abstract present ({len(paper['abstract'])} chars)")
                else:
                    fail("  No abstract for found paper", "Embedding gate NEEDS this to verify claims")
            else:
                fail(f"Did NOT find 'Attention Is All You Need'", f"Status: {r1.get('status')}, Reason: {r1.get('reason', 'unknown')}")

            # Check BERT
            r2 = results.get("2", {})
            if r2.get("status") == "found":
                ok(f"Found BERT paper (score: {r2.get('match_score', '?')})")
            else:
                fail(f"Did NOT find BERT paper", f"Reason: {r2.get('reason', 'unknown')}")

            # Check fake paper
            r3 = results.get("3", {})
            if r3.get("status") == "not_found":
                ok("Correctly reported fake paper as not_found")
            else:
                fail("Fake paper was reported as found!", "False positive in existence check")
        else:
            fail(f"Existence agent returned error: {result.error}")
    except Exception as e:
        fail(f"Existence agent threw: {e}")
        traceback.print_exc()


# ── Test 7: Existence Match Scoring ─────────────────────────────────────────

async def test_match_scoring():
    """Test the _find_best_match scoring logic with edge cases."""
    section("7. Match Scoring Logic")

    from server.agents.existence import ExistenceAgent, word_overlap, normalize_text, get_first_author_lastname

    agent = ExistenceAgent()

    # Test normalize_text
    assert normalize_text("Hello, World!") == "hello world", "normalize_text failed"
    ok("normalize_text works")

    # Test get_first_author_lastname
    assert get_first_author_lastname("Vaswani et al.") == "vaswani", \
        f"Expected 'vaswani', got '{get_first_author_lastname('Vaswani et al.')}'"
    ok("get_first_author_lastname works")

    # Test word_overlap
    sim = word_overlap("Attention Is All You Need", "Attention Is All You Need")
    assert sim == 1.0, f"Self-similarity should be 1.0, got {sim}"
    ok("word_overlap: self-similarity = 1.0")

    sim = word_overlap("Attention Is All You Need", "Completely Different Title")
    assert sim < 0.3, f"Unrelated titles should have low overlap, got {sim}"
    ok(f"word_overlap: unrelated titles = {sim:.2f}")

    # Test _find_best_match with realistic data
    reference = {
        "title": "Attention Is All You Need",
        "year": 2017,
        "authors": "Vaswani et al."
    }

    # Simulate Semantic Scholar results
    results = [
        {
            "title": "Attention Is All You Need",
            "year": 2017,
            "authors": [{"name": "Ashish Vaswani"}, {"name": "Noam Shazeer"}],
        },
        {
            "title": "Effective Approaches to Attention-based Neural Machine Translation",
            "year": 2015,
            "authors": [{"name": "Minh-Thang Luong"}],
        },
    ]

    match, score = agent._find_best_match(reference, results)
    if match and match.get("title") == "Attention Is All You Need":
        ok(f"Correctly matched exact paper (score: {score})")
    else:
        fail(f"Failed to match exact paper", f"Got: {match}, score: {score}")

    # Edge case: partial title match
    partial_ref = {
        "title": "Attention",
        "year": 2017,
        "authors": "Vaswani"
    }
    match2, score2 = agent._find_best_match(partial_ref, results)
    print(f"  [INFO] Partial title match: score={score2}, matched={'yes' if match2 else 'no'}")
    if score2 < 60:
        print(f"  [INFO] Partial match below threshold (60) - this is correct behavior")


# ── Test 8: S2 API Key Passthrough ──────────────────────────────────────────

async def test_api_key_passthrough():
    """Verify API keys are actually passed to API clients."""
    section("8. API Key Configuration")

    from server.config import settings
    from server.agents.existence import ExistenceAgent
    from server.agents.fetcher import FetcherAgent

    # Check existence agent
    existence = ExistenceAgent()
    if existence.semantic_scholar.api_key:
        ok("Existence agent has S2 API key")
    else:
        fail("Existence agent has NO S2 API key",
             "SemanticScholarAPI() called without api_key from settings.S2_API_KEY")

    # Check fetcher agent
    fetcher = FetcherAgent()
    if fetcher.semantic_scholar.api_key:
        ok("Fetcher agent has S2 API key")
    else:
        fail("Fetcher agent has NO S2 API key",
             "SemanticScholarAPI() called without api_key from settings.S2_API_KEY")

    # Check settings
    if settings.S2_API_KEY:
        ok(f"S2_API_KEY is set in settings")
    else:
        print("  [WARN] S2_API_KEY not configured in .env")
        print("         Both agents create SemanticScholarAPI() without passing it")
        print("         FIX: Pass settings.S2_API_KEY to SemanticScholarAPI(api_key=...)")

    if settings.GEMINI_API_KEY:
        ok("GEMINI_API_KEY is set")
    else:
        fail("GEMINI_API_KEY not set", "Extractor agent requires this for citation extraction")


# ── Test 9: Full Pipeline Smoke Test ────────────────────────────────────────

async def test_pipeline_smoke():
    """Run the full pipeline with sample text and report what happens at each stage."""
    section("9. Pipeline Smoke Test (Sample Paper)")

    from server.core.pipeline import PipelineOrchestrator
    from server.agents.base import registry

    pipeline = PipelineOrchestrator()
    progress_log = []

    async def on_progress(message, progress):
        progress_log.append((progress, message))
        print(f"    [{progress:3.0f}%] {message}")

    sample_text = """
    Attention mechanisms have become a fundamental component in neural networks.
    The Transformer architecture (Vaswani et al., 2017) introduced self-attention
    as a replacement for recurrence. BERT (Devlin et al., 2019) showed that
    bidirectional pre-training leads to improvements. GPT-2 (Radford et al., 2019)
    demonstrated coherent text generation.

    References:
    [1] Vaswani et al. Attention Is All You Need, 2017.
    [2] Devlin et al. BERT: Pre-training of Deep Bidirectional Transformers, 2019.
    [3] Radford et al. Language Models are Unsupervised Multitask Learners, 2019.
    """

    try:
        report = await pipeline.run(text=sample_text, on_progress=on_progress)

        print(f"\n  Pipeline Results:")
        print(f"    Total citations:  {report.get('total_citations', 0)}")
        print(f"    Integrity score:  {report.get('integrity_score', 0)}")
        print(f"    Summary:          {json.dumps(report.get('summary', {}))}")
        print(f"    Tokens used:      {report.get('stats', {}).get('total_tokens', 0)}")
        print(f"    Latency:          {report.get('stats', {}).get('latency_ms', 0):.0f}ms")

        if report.get("total_citations", 0) > 0:
            ok(f"Pipeline produced {report['total_citations']} citations")
        else:
            fail("Pipeline produced 0 citations",
                 "Likely cause: extractor failed (needs GEMINI_API_KEY) "
                 "or the real extractor is using the dummy one's regex which needs [N] style refs")

        # Check individual citations
        for cit in report.get("citations", []):
            status = cit.get("existence_status", "unknown")
            verdict = cit.get("verification", {}).get("verdict", "none") if cit.get("verification") else "no_verification"
            print(f"    Citation #{cit.get('id')}: existence={status}, verdict={verdict}")

    except Exception as e:
        fail(f"Pipeline threw: {e}")
        traceback.print_exc()


# ── Test 10: Upload Endpoint Bug ────────────────────────────────────────────

async def test_upload_endpoint_bug():
    """Check if upload endpoint actually passes PDF content to pipeline."""
    section("10. Upload Endpoint Analysis")

    from server.api.routes import router

    # Find the upload route handler
    for route in router.routes:
        if hasattr(route, "path") and route.path == "/analyze/upload":
            # Read the source to check if PDF content is passed
            import inspect
            source = inspect.getsource(route.endpoint)
            if 'text=""' in source and "content" not in source.split("pipeline.run")[0] if "pipeline.run" in source else True:
                fail("Upload endpoint reads PDF but passes empty text to pipeline",
                     "PDF content is read (line 117) but pipeline.run() gets text='' (line 136)")
            break


# ── Main ─────────────────────────────────────────────────────────────────────

async def main():
    print("=" * 60)
    print("  Resify Backend Retrieval Diagnostics")
    print("=" * 60)

    await test_agent_registration()
    await test_semantic_scholar_api()
    await test_arxiv_api()
    await test_fetcher_agent()
    await test_extractor_agent()
    await test_existence_agent()
    await test_match_scoring()
    await test_api_key_passthrough()
    await test_pipeline_smoke()
    await test_upload_endpoint_bug()

    # Cleanup aiohttp session
    try:
        from server.utils.apis import APIClientManager
        await APIClientManager.close_session()
    except Exception:
        pass

    print(f"\n{'='*60}")
    print(f"  Results: {PASS} passed, {FAIL} failed")
    print(f"{'='*60}")

    if FAIL > 0:
        print("\n  KEY ISSUES TO FIX:")
        print("  1. Set GEMINI_API_KEY in .env (extractor needs it)")
        print("  2. Pass settings.S2_API_KEY to SemanticScholarAPI() in agents")
        print("  3. Fix upload endpoint to pass PDF content to pipeline")
        print("  4. Build real embedding_gate agent for pipeline")


if __name__ == "__main__":
    asyncio.run(main())
