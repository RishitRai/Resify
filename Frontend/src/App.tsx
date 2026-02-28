import { useState, useRef, useEffect } from 'react';
import './index.css';
import './App.css';
import { ResearchHeader } from './components/ResearchHeader';
import { KnowledgeGraph } from './components/KnowledgeGraph';
import type { NodeData, EdgeData } from './components/KnowledgeGraph';
import { AgentStream } from './components/AgentStream';
import type { LogEvent } from './components/AgentStream';
import { SynthesisPanel } from './components/SynthesisPanel';
import { StatCards } from './components/StatCards';

// ── Mock Simulation Data ──────────────────────────────────
const INITIAL_NODE: NodeData = {
  id: 'root', label: 'Paper Integrity Scan', type: 'root',
  status: 'active', x: 50, y: 14
};

const MOCK_NODES: NodeData[] = [
  INITIAL_NODE,
  { id: 'a1', label: 'Citation Verifier', type: 'agent', status: 'pending', x: 18, y: 38 },
  { id: 'a2', label: 'Claim Extractor', type: 'agent', status: 'pending', x: 50, y: 38 },
  { id: 'a3', label: 'LLM Detector', type: 'agent', status: 'pending', x: 82, y: 38 },
  { id: 'a4', label: 'Cross-Ref DB', type: 'agent', status: 'pending', x: 34, y: 62 },
];

const MOCK_EDGES: EdgeData[] = [
  { source: 'root', target: 'a1', active: false },
  { source: 'root', target: 'a2', active: false },
  { source: 'root', target: 'a3', active: false },
  { source: 'root', target: 'a4', active: false },
];

// -- Mock Logic Removal in Progress --

// ── App ───────────────────────────────────────────────────
type Phase = 'idle' | 'analyzing' | 'synthesis';

export default function App() {
  const [phase, setPhase] = useState<Phase>('idle');
  const [nodes, setNodes] = useState<NodeData[]>([INITIAL_NODE]);
  const [edges, setEdges] = useState<EdgeData[]>([]);
  const [logs, setLogs] = useState<LogEvent[]>([]);
  const [showCompactSearch, setShowCompactSearch] = useState(false);
  const [heroQuery, setHeroQuery] = useState('');
  const heroSearchRef = useRef<HTMLDivElement>(null);

  // Show compact header search when the hero search scrolls out of view
  useEffect(() => {
    if (phase !== 'idle') return;
    const el = heroSearchRef.current;
    if (!el) return;
    const observer = new IntersectionObserver(
      ([entry]) => setShowCompactSearch(!entry.isIntersecting),
      { threshold: 0, rootMargin: '-73px 0px 0px 0px' }  // offset by header height
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [phase]);

  const [report, setReport] = useState<any>(null);

  const handleAnalyze = (query: string) => {
    if (phase !== 'idle' || !query.trim()) return;

    setPhase('analyzing');
    setLogs([]);
    setNodes([INITIAL_NODE]);
    setEdges([]);
    setReport(null);

    const ws = new WebSocket('ws://127.0.0.1:8000/ws/analyze');

    ws.onopen = () => {
      ws.send(JSON.stringify({ paper_input: query }));

      // Fan out agents visually
      setNodes(MOCK_NODES.map(n => ({ ...n, status: n.id === 'root' ? 'active' : 'active' })));
      setEdges(MOCK_EDGES.map(e => ({ ...e, active: true })));
    };

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);

      if (data.type === 'progress') {
        setLogs(prev => [...prev, {
          id: `${Date.now()}-${Math.random()}`,
          timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }),
          agent: 'Pipeline',
          message: data.message,
          type: 'info'
        }]);

        // Update nodes based on progress messages
        if (data.message.toLowerCase().includes('fetching')) {
          setNodes(prev => prev.map(n => n.id === 'root' ? { ...n, status: 'active' } : n));
        } else if (data.message.toLowerCase().includes('extracting')) {
          setNodes(prev => prev.map(n => n.id === 'a2' ? { ...n, status: 'active' } : n));
        } else if (data.message.toLowerCase().includes('existence')) {
          setNodes(prev => prev.map(n => n.id === 'a1' ? { ...n, status: 'active' } : n));
        }
      }
      else if (data.type === 'result') {
        const fullReport = data.report;
        setReport(fullReport);
        setPhase('synthesis');

        setLogs(prev => [...prev, {
          id: `${Date.now()}-${Math.random()}`,
          timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }),
          agent: 'System',
          message: 'Analysis complete. Report synthesised.',
          type: 'success'
        }]);

        // Final node statuses
        setNodes(prev => prev.map(n => ({
          ...n,
          status: 'complete'
        })));
        setEdges(prev => prev.map(e => ({ ...e, active: false })));
        ws.close();
      }
      else if (data.type === 'error') {
        setLogs(prev => [...prev, {
          id: `${Date.now()}-${Math.random()}`,
          timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }),
          agent: 'Error',
          message: data.message,
          type: 'error'
        }]);
        setPhase('idle');
        ws.close();
      }
    };

    ws.onerror = () => {
      setLogs(prev => [...prev, {
        id: 'ws-error',
        timestamp: new Date().toLocaleTimeString(),
        agent: 'System',
        message: 'WebSocket connection failed. Ensure backend is running.',
        type: 'error'
      }]);
      setPhase('idle');
    };
  };

  return (
    <div className="app-shell">
      <ResearchHeader
        onAnalyze={handleAnalyze}
        isAnalyzing={phase === 'analyzing'}
        showCompactSearch={showCompactSearch && phase === 'idle'}
      />

      {phase === 'idle' ? (
        <div className="hero-section">
          {/* ── Above-fold ── */}
          <div className="hero-above-fold">
            <div className="hero-inner">
              <div className="hero-eyebrow label">Research Integrity Scanner</div>
              <h2 className="hero-headline">
                Is the paper <em>really</em> real?
              </h2>
              <p className="hero-body">
                Paste any ArXiv URL or DOI. PaperShield dispatches six parallel agents
                to verify every citation, cross-reference every claim, and detect
                AI-generated sections — in under 15 seconds.
              </p>

              {/* ── Hero Search ── */}
              <div className="hero-search-wrap" ref={heroSearchRef}>
                <div className="hero-search-inner">
                  <input
                    type="text"
                    className="hero-search-input mono"
                    placeholder="Paste arXiv URL, DOI, or paper description…"
                    value={heroQuery}
                    onChange={e => setHeroQuery(e.target.value)}
                    onKeyDown={e => e.key === 'Enter' && handleAnalyze(heroQuery)}
                  />
                  <button
                    className="hero-search-btn"
                    onClick={() => handleAnalyze(heroQuery)}
                    disabled={!heroQuery.trim()}
                  >
                    Scan Paper →
                  </button>
                </div>
                <p className="hero-search-hint mono">e.g. https://arxiv.org/abs/2602.04561</p>
                <div className="hero-agents">
                  <span className="hero-agents-label">Agents:</span>
                  {['Citation Verifier', 'AI Detector', 'Claim Checker', 'Author Credibility', 'Methodology', 'Stats Anomaly'].map(a => (
                    <span key={a} className="hero-agent-chip">{a}</span>
                  ))}
                </div>
              </div>

              <div className="hero-scroll-hint label">
                ↓ Scroll to see why peer review is broken
              </div>
            </div>
          </div>

          {/* ── Evidence Cards ── */}
          <StatCards />
        </div>
      ) : (
        <div className="dashboard-grid anim-fade-up">
          {/* Investigation map */}
          <div className="panel-graph">
            <KnowledgeGraph nodes={nodes} edges={edges} />
            {phase === 'synthesis' && report && (
              <SynthesisPanel
                data={{
                  trustScore: report.integrity_score,
                  totalCitations: report.total_citations,
                  verified: report.summary?.supported || 0,
                  suspicious: report.summary?.uncertain || 0,
                  fabricated: report.summary?.not_found || 0,
                  aiProbability: report.stats?.ai_probability || 0,
                  conclusion: report.summary?.conclusion || "Analysis complete."
                }}
              />
            )}
          </div>

          {/* Agent log */}
          <div className="panel-stream">
            <AgentStream logs={logs} />
          </div>
        </div>
      )}
    </div>
  );
}
