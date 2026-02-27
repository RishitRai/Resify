import './SynthesisPanel.css';

interface SynthesisData {
    trustScore: number;
    totalCitations: number;
    verified: number;
    suspicious: number;
    fabricated: number;
    aiProbability: number;
    conclusion: string;
}

export function SynthesisPanel({ data }: { data: SynthesisData }) {
    const verdict =
        data.trustScore >= 90 ? 'PASS' :
            data.trustScore >= 70 ? 'CAUTION' : 'HIGH RISK';

    const verdictClass =
        data.trustScore >= 90 ? 'verdict-pass' :
            data.trustScore >= 70 ? 'verdict-caution' : 'verdict-fail';

    return (
        <div className="synthesis-overlay anim-fade-up">
            <div className="synthesis-card">
                {/* Card header */}
                <div className="synth-head rule-bottom">
                    <span className="label">Integrity Report</span>
                    <span className="synth-ts mono">{new Date().toLocaleTimeString()}</span>
                </div>

                {/* Verdict block */}
                <div className={`synth-verdict ${verdictClass}`}>
                    <div className="verdict-score mono">{data.trustScore}</div>
                    <div className="verdict-right">
                        <div className="verdict-label">Trust Score</div>
                        <div className={`verdict-flag ${verdictClass}`}>{verdict}</div>
                    </div>
                </div>

                {/* Citation grid */}
                <div className="synth-citations rule-top">
                    <div className="label" style={{ padding: '0.6rem 0', borderBottom: '1px solid var(--rule)' }}>Citation Audit</div>
                    <div className="cit-grid">
                        <div className="cit-cell">
                            <div className="cit-num mono">{data.totalCitations}</div>
                            <div className="label">Total</div>
                        </div>
                        <div className="cit-cell cit-ok">
                            <div className="cit-num mono">{data.verified}</div>
                            <div className="label">Verified</div>
                        </div>
                        <div className="cit-cell cit-warn">
                            <div className="cit-num mono">{data.suspicious}</div>
                            <div className="label">Suspect</div>
                        </div>
                        <div className={`cit-cell cit-fail ${data.fabricated > 0 ? 'cit-fail-active' : ''}`}>
                            <div className="cit-num mono">{data.fabricated}</div>
                            <div className="label">Fabricated</div>
                        </div>
                    </div>
                </div>

                {/* AI probability bar */}
                <div className="synth-ai rule-top">
                    <div className="ai-row">
                        <span className="label">AI Generation Probability</span>
                        <span className="mono ai-pct">{data.aiProbability}%</span>
                    </div>
                    <div className="ai-bar-track">
                        <div
                            className={`ai-bar-fill ${data.aiProbability > 50 ? 'ai-high' : 'ai-low'}`}
                            style={{ width: `${data.aiProbability}%` }}
                        ></div>
                    </div>
                </div>

                {/* Conclusion */}
                <div className="synth-conclusion rule-top">
                    <div className="label" style={{ marginBottom: '0.5rem' }}>Synthesis</div>
                    <p className="conclusion-text">{data.conclusion}</p>
                </div>
            </div>
        </div>
    );
}
