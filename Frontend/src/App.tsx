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

const MOCK_LOG_SEQUENCE: Array<{
  delay: number; agent: string; message: string; type: LogEvent['type'];
  mutation?: () => void;
}> = [
    { delay: 300, agent: 'System', message: 'PaperShield v2 initialising deep inspection protocols.', type: 'info' },
    { delay: 900, agent: 'System', message: 'Document parsed: 43 citations discovered. Dispatching 4 agents.', type: 'info' },
    { delay: 1800, agent: 'Citation Verifier', message: 'Connecting to Semantic Scholar / CrossRef APIs…', type: 'info' },
    { delay: 2600, agent: 'LLM Detector', message: 'Scanning §3.2 for watermark patterns and perplexity signature.', type: 'info' },
    { delay: 3400, agent: 'Claim Extractor', message: 'Extracting primary claims vs cited source abstracts.', type: 'info' },
    { delay: 4200, agent: 'Cross-Ref DB', message: 'Running venue + author registry checks on 43 entries.', type: 'info' },
    { delay: 5200, agent: 'Citation Verifier', message: 'Citations [1]–[12] confirmed valid DOIs.', type: 'success' },
    { delay: 6400, agent: 'Citation Verifier', message: 'ERROR — Citation [14]: DOI not found. "J. Smith 2024" has zero registry results.', type: 'error' },
    { delay: 7600, agent: 'Cross-Ref DB', message: 'ALERT — Citation [22] venue is hallucinated. Confirmed AI-fabricated reference.', type: 'error' },
    { delay: 8800, agent: 'Claim Extractor', message: 'Contradiction — Paper claims [4] showed 30% gain; actual abstract: "no significant difference".', type: 'warning' },
    { delay: 10200, agent: 'LLM Detector', message: '§4 (Results): 92% AI-generation probability. High verbosity, generic bullet point structure.', type: 'error' },
    { delay: 11800, agent: 'Citation Verifier', message: 'All 43 citations processed. 3 confirmed fabrications.', type: 'info' },
    { delay: 12800, agent: 'System', message: 'All agents returned. Synthesising final report.', type: 'info' },
  ];

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

  const handleAnalyze = (query: string) => {
    if (phase !== 'idle' || !query.trim()) return;

    setPhase('analyzing');
    setLogs([]);
    setNodes([INITIAL_NODE]);
    setEdges([]);

    // Fan out agents after brief delay
    setTimeout(() => {
      setNodes(MOCK_NODES.map(n => ({ ...n, status: n.id === 'root' ? 'active' : 'active' })));
      setEdges(MOCK_EDGES.map(e => ({ ...e, active: true })));
    }, 600);

    // Stream logs
    MOCK_LOG_SEQUENCE.forEach(({ delay, agent, message, type }) => {
      setTimeout(() => {
        setLogs(prev => [...prev, {
          id: `${Date.now()}-${Math.random()}`,
          timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }),
          agent, message, type
        }]);

        // Visual mutations
        if (type === 'error' && agent === 'Citation Verifier' && message.includes('[14]')) {
          setNodes(prev => [
            ...prev,
            { id: 'c1', label: 'Fake DOI — [14]', type: 'contradiction', status: 'complete', x: 8, y: 62 }
          ]);
          setEdges(prev => [...prev, { source: 'a1', target: 'c1', active: false }]);
        }
        if (type === 'error' && agent === 'Cross-Ref DB') {
          setNodes(prev => [
            ...prev,
            { id: 'c2', label: 'AI Venue — [22]', type: 'contradiction', status: 'complete', x: 24, y: 80 }
          ]);
          setEdges(prev => [...prev, { source: 'a4', target: 'c2', active: false }]);
        }
        if (type === 'error' && agent === 'LLM Detector') {
          setNodes(prev => prev.map(n => n.id === 'a3' ? { ...n, status: 'error' } : n));
        }
        if (agent === 'System' && message.includes('Synthesising')) {
          setNodes(prev => prev.map(n => ({
            ...n,
            status: n.type === 'contradiction' ? 'error'
              : n.type === 'agent' ? 'complete'
                : 'complete'
          })));
          setEdges(prev => prev.map(e => ({ ...e, active: false })));
        }
      }, delay);
    });

    // Show synthesis panel
    setTimeout(() => setPhase('synthesis'), 13200);
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

          {/* ── Footer ── */}
          <footer className="app-footer">
            <div className="footer-left mono">© 2026 Resify Inc.</div>
            <div className="footer-right">
              <span className="footer-link label">Privacy</span>
              <span className="footer-link label">Terms</span>
              <span className="footer-link label">Contact</span>
            </div>
          </footer>
        </div>
      ) : (
        <div className="dashboard-grid anim-fade-up">
          {/* Investigation map */}
          <div className="panel-graph">
            <KnowledgeGraph nodes={nodes} edges={edges} />
            {phase === 'synthesis' && (
              <SynthesisPanel
                data={{
                  trustScore: 32,
                  totalCitations: 43,
                  verified: 38,
                  suspicious: 2,
                  fabricated: 3,
                  aiProbability: 85,
                  conclusion:
                    'HIGH RISK. Three confirmed hallucinated citations, one misattributed claim, and §4 shows strong signatures of AI generation. Recommend rejection pending author clarification.'
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
