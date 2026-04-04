import { useState, useRef, useEffect, useCallback } from 'react';
import './index.css';
import './App.css';
import { ResearchHeader } from './components/ResearchHeader';
import { KnowledgeGraph } from './components/KnowledgeGraph';
import type { NodeData, EdgeData } from './components/KnowledgeGraph';
import { AgentStream } from './components/AgentStream';
import type { LogEvent } from './components/AgentStream';
import { SynthesisPanel } from './components/SynthesisPanel';
import { StatCards } from './components/StatCards';

// ── Types ────────────────────────────────────────────────
type Phase = 'idle' | 'analyzing' | 'synthesis';

interface PipelineReport {
  integrity_score: number;
  total_citations: number;
  summary: {
    supported: number;
    contradicted: number;
    uncertain: number;
    not_found: number;
    metadata_errors: number;
  };
  paper: {
    title?: string;
    authors?: string[];
    year?: number;
    source?: string;
  };
  citations: Array<{
    id: number;
    claim: string;
    reference: { authors?: string; title?: string; year?: number };
    existence_status: string;
    verification?: { verdict: string; confidence: number; evidence?: string; method: string } | null;
  }>;
  stats: {
    total_tokens: number;
    latency_ms: number;
  };
}

// ── Stage → agent node mapping ───────────────────────────
const STAGE_AGENTS: Record<string, { id: string; label: string; x: number; y: number }> = {
  fetching:            { id: 'a1', label: 'Fetcher',           x: 14, y: 38 },
  extracting:          { id: 'a2', label: 'Citation Extractor', x: 38, y: 38 },
  checking_existence:  { id: 'a3', label: 'Existence Checker', x: 62, y: 38 },
  embedding_gate:      { id: 'a4', label: 'Embedding Gate',    x: 86, y: 38 },
  llm_verification:    { id: 'a5', label: 'LLM Verifier',     x: 38, y: 62 },
  synthesizing:        { id: 'a6', label: 'Synthesizer',       x: 62, y: 62 },
};

const ROOT_NODE: NodeData = {
  id: 'root', label: 'Paper Integrity Scan', type: 'root',
  status: 'active', x: 50, y: 10,
};

function buildInitialNodes(): NodeData[] {
  return [
    ROOT_NODE,
    ...Object.values(STAGE_AGENTS).map(a => ({
      id: a.id, label: a.label, type: 'agent' as const,
      status: 'pending' as const, x: a.x, y: a.y,
    })),
  ];
}

function buildInitialEdges(): EdgeData[] {
  return Object.values(STAGE_AGENTS).map(a => ({
    source: 'root', target: a.id, active: false,
  }));
}

// Map progress message to the stage that's running
function detectStage(message: string): string | null {
  const lower = message.toLowerCase();
  if (lower.includes('fetching'))    return 'fetching';
  if (lower.includes('extracting')) return 'extracting';
  if (lower.includes('existence') || lower.includes('checking')) return 'checking_existence';
  if (lower.includes('verifying') || lower.includes('embedding')) return 'embedding_gate';
  if (lower.includes('deep verification') || lower.includes('llm')) return 'llm_verification';
  if (lower.includes('generating') || lower.includes('synthesiz')) return 'synthesizing';
  return null;
}

function stageToAgent(stage: string): string {
  return STAGE_AGENTS[stage]?.label ?? 'Pipeline';
}

// ── App ──────────────────────────────────────────────────
export default function App() {
  const [phase, setPhase] = useState<Phase>('idle');
  const [nodes, setNodes] = useState<NodeData[]>([ROOT_NODE]);
  const [edges, setEdges] = useState<EdgeData[]>([]);
  const [logs, setLogs] = useState<LogEvent[]>([]);
  const [report, setReport] = useState<PipelineReport | null>(null);
  const [showCompactSearch, setShowCompactSearch] = useState(false);
  const [heroQuery, setHeroQuery] = useState('');
  const [errorMsg, setErrorMsg] = useState('');
  const heroSearchRef = useRef<HTMLDivElement>(null);
  const wsRef = useRef<WebSocket | null>(null);

  // Show compact header search when the hero search scrolls out of view
  useEffect(() => {
    if (phase !== 'idle') return;
    const el = heroSearchRef.current;
    if (!el) return;
    const observer = new IntersectionObserver(
      ([entry]) => setShowCompactSearch(!entry.isIntersecting),
      { threshold: 0, rootMargin: '-73px 0px 0px 0px' }
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [phase]);

  const addLog = useCallback((agent: string, message: string, type: LogEvent['type']) => {
    setLogs(prev => [...prev, {
      id: `${Date.now()}-${Math.random()}`,
      timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }),
      agent,
      message,
      type,
    }]);
  }, []);

  const activateStage = useCallback((stage: string) => {
    const agentInfo = STAGE_AGENTS[stage];
    if (!agentInfo) return;

    setNodes(prev => prev.map(n =>
      n.id === agentInfo.id
        ? { ...n, status: 'active' }
        : n
    ));
    setEdges(prev => prev.map(e =>
      e.target === agentInfo.id
        ? { ...e, active: true }
        : e
    ));
  }, []);

  const completeStage = useCallback((stage: string) => {
    const agentInfo = STAGE_AGENTS[stage];
    if (!agentInfo) return;

    setNodes(prev => prev.map(n =>
      n.id === agentInfo.id
        ? { ...n, status: 'complete' }
        : n
    ));
    setEdges(prev => prev.map(e =>
      e.target === agentInfo.id
        ? { ...e, active: false }
        : e
    ));
  }, []);

  const handleResult = useCallback((pipelineReport: PipelineReport) => {
    setReport(pipelineReport);

    // Add finding nodes for notable citations
    const newNodes: NodeData[] = [];
    const newEdges: EdgeData[] = [];
    let findingY = 85;

    for (const cit of pipelineReport.citations) {
      if (cit.existence_status === 'not_found') {
        const nodeId = `nf-${cit.id}`;
        newNodes.push({
          id: nodeId,
          label: `Not Found #${cit.id}`,
          type: 'contradiction',
          status: 'error',
          x: 20 + (cit.id * 12) % 60,
          y: findingY,
        });
        newEdges.push({ source: 'a3', target: nodeId, active: false });
        findingY += 5;
      } else if (cit.verification?.verdict === 'contradicted') {
        const nodeId = `ct-${cit.id}`;
        newNodes.push({
          id: nodeId,
          label: `Contradicted #${cit.id}`,
          type: 'contradiction',
          status: 'error',
          x: 20 + (cit.id * 15) % 60,
          y: findingY,
        });
        newEdges.push({ source: 'a4', target: nodeId, active: false });
        findingY += 5;
      }
    }

    if (newNodes.length > 0) {
      setNodes(prev => [...prev, ...newNodes]);
      setEdges(prev => [...prev, ...newEdges]);
    }

    // Mark all agents complete, root complete
    setNodes(prev => prev.map(n => {
      if (n.type === 'agent') return { ...n, status: 'complete' };
      if (n.type === 'root') return { ...n, status: 'complete' };
      return n;
    }));

    addLog('Pipeline', `Analysis complete. Integrity score: ${pipelineReport.integrity_score}%`, 'success');
    setPhase('synthesis');
  }, [addLog]);

  const handleAnalyze = useCallback((query: string) => {
    if (phase !== 'idle' || !query.trim()) return;

    // Reset state
    setPhase('analyzing');
    setLogs([]);
    setReport(null);
    setErrorMsg('');
    setNodes(buildInitialNodes());
    setEdges(buildInitialEdges());

    addLog('System', `Starting analysis: ${query}`, 'info');

    // Open WebSocket
    const wsProtocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const ws = new WebSocket(`${wsProtocol}//${window.location.host}/ws/analyze`);
    wsRef.current = ws;

    let lastStage: string | null = null;

    ws.onopen = () => {
      ws.send(JSON.stringify({ paper_input: query }));
      addLog('System', 'Connected to analysis pipeline.', 'info');
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);

        if (data.type === 'progress') {
          const stage = detectStage(data.message);

          // Complete previous stage if we moved to a new one
          if (stage && stage !== lastStage) {
            if (lastStage) completeStage(lastStage);
            activateStage(stage);
            lastStage = stage;
          }

          // Detect completed messages
          if (data.message.toLowerCase().startsWith('completed') && lastStage) {
            completeStage(lastStage);
          }

          const agentName = stage ? stageToAgent(stage) : 'Pipeline';
          addLog(agentName, data.message, 'info');
        }

        else if (data.type === 'result' && data.report) {
          if (lastStage) completeStage(lastStage);
          handleResult(data.report);
        }

        else if (data.type === 'error') {
          addLog('System', `Error: ${data.message}`, 'error');
          setErrorMsg(data.message);
        }
      } catch {
        // non-JSON message, ignore
      }
    };

    ws.onerror = () => {
      addLog('System', 'Connection error. Is the backend running on port 8000?', 'error');
      setErrorMsg('Could not connect to backend. Start it with: python -m uvicorn server.main:app --reload --port 8000');
    };

    ws.onclose = () => {
      if (phase === 'analyzing' && !report) {
        // Connection closed before result — only add log if no error already shown
      }
    };
  }, [phase, addLog, activateStage, completeStage, handleResult]);

  // Cleanup WebSocket on unmount
  useEffect(() => {
    return () => { wsRef.current?.close(); };
  }, []);

  const handleReset = () => {
    wsRef.current?.close();
    setPhase('idle');
    setNodes([ROOT_NODE]);
    setEdges([]);
    setLogs([]);
    setReport(null);
    setErrorMsg('');
    setHeroQuery('');
  };

  // Build synthesis data from real report
  const paperKey = report?.paper?.title
    ? `${report.paper.title}_${report.paper.year || ''}`
    : heroQuery;

  const synthesisData = report ? {
    trustScore: report.integrity_score,
    totalCitations: report.total_citations,
    supported: report.summary.supported,
    contradicted: report.summary.contradicted,
    uncertain: report.summary.uncertain,
    notFound: report.summary.not_found,
    metadataErrors: report.summary.metadata_errors,
    conclusion: buildConclusion(report),
    citations: report.citations,
    paperKey,
  } : null;

  return (
    <div className="app-shell">
      <ResearchHeader
        onAnalyze={handleAnalyze}
        isAnalyzing={phase === 'analyzing'}
        showCompactSearch={showCompactSearch && phase === 'idle'}
      />

      {phase === 'idle' ? (
        <div className="hero-section">
          <div className="hero-above-fold">
            <div className="hero-inner">
              <div className="hero-eyebrow label">Research Integrity Scanner</div>
              <h2 className="hero-headline">
                Is the paper <em>really</em> real?
              </h2>
              <p className="hero-body">
                Paste any ArXiv URL or DOI. Resify dispatches parallel agents
                to verify every citation, cross-reference every claim, and assess
                research integrity — in seconds.
              </p>

              <div className="hero-search-wrap" ref={heroSearchRef}>
                <div className="hero-search-inner">
                  <input
                    type="text"
                    className="hero-search-input mono"
                    placeholder="Paste arXiv URL, DOI, or paper description..."
                    value={heroQuery}
                    onChange={e => setHeroQuery(e.target.value)}
                    onKeyDown={e => e.key === 'Enter' && handleAnalyze(heroQuery)}
                  />
                  <button
                    className="hero-search-btn"
                    onClick={() => handleAnalyze(heroQuery)}
                    disabled={!heroQuery.trim()}
                  >
                    Scan Paper
                  </button>
                </div>
                <p className="hero-search-hint mono">e.g. https://arxiv.org/abs/1706.03762</p>
                <div className="hero-agents">
                  <span className="hero-agents-label">Agents:</span>
                  {['Fetcher', 'Citation Extractor', 'Existence Checker', 'Embedding Gate', 'LLM Verifier', 'Synthesizer'].map(a => (
                    <span key={a} className="hero-agent-chip">{a}</span>
                  ))}
                </div>
              </div>

              <div className="hero-scroll-hint label">
                Scroll to see why peer review is broken
              </div>
            </div>
          </div>

          <StatCards />

          <footer className="app-footer">
            <div className="footer-left mono">Resify 2026</div>
            <div className="footer-right">
              <span className="footer-link label">Privacy</span>
              <span className="footer-link label">Terms</span>
              <span className="footer-link label">Contact</span>
            </div>
          </footer>
        </div>
      ) : (
        <div className="dashboard-grid anim-fade-up">
          <div className="panel-graph">
            <KnowledgeGraph nodes={nodes} edges={edges} />
            {phase === 'synthesis' && synthesisData && (
              <SynthesisPanel data={synthesisData} />
            )}
            {errorMsg && (
              <div className="error-banner">
                <p>{errorMsg}</p>
                <button onClick={handleReset} className="error-reset-btn">Try Again</button>
              </div>
            )}
          </div>

          <div className="panel-stream">
            <AgentStream logs={logs} />
            {phase === 'synthesis' && (
              <button onClick={handleReset} className="reset-btn">
                Scan Another Paper
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function buildConclusion(report: PipelineReport): string {
  const { summary, total_citations } = report;
  const found = total_citations - summary.not_found;
  const parts: string[] = [];

  parts.push(`${found} of ${total_citations} citations were located in academic databases.`);

  if (summary.supported > 0) {
    parts.push(`${summary.supported} verified as consistent with source material.`);
  }
  if (summary.contradicted > 0) {
    parts.push(`${summary.contradicted} may conflict with the cited source — manual review recommended.`);
  }
  if (summary.uncertain > 0) {
    parts.push(`${summary.uncertain} could not be confidently assessed.`);
  }
  if (summary.not_found > 0) {
    parts.push(`${summary.not_found} were not found in Semantic Scholar (may exist in other databases).`);
  }

  return parts.join(' ');
}
