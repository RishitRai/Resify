import './SynthesisPanel.css';
import { useState, useEffect, useCallback } from 'react';

interface CitationDetail {
    id: number;
    claim: string;
    reference: { authors?: string; title?: string; year?: number };
    existence_status: string;
    verification?: { verdict: string; confidence: number; evidence?: string; method: string } | null;
    source_found?: { title?: string; authors?: string[]; year?: number } | null;
}

interface SynthesisData {
    trustScore: number;
    totalCitations: number;
    supported: number;
    contradicted: number;
    uncertain: number;
    notFound: number;
    metadataErrors: number;
    conclusion: string;
    citations: CitationDetail[];
    paperKey: string;
}

interface Override {
    verdict: string;
    notes: string;
}

type FilterMode = 'all' | 'supported' | 'contradicted' | 'uncertain' | 'not_found';

const VERDICT_OPTIONS = [
    { value: 'supported', label: 'Verified', cls: 'status-ok' },
    { value: 'contradicted', label: 'Conflict', cls: 'status-conflict' },
    { value: 'uncertain', label: 'Unclear', cls: 'status-uncertain' },
    { value: 'not_found', label: 'Not Found', cls: 'status-notfound' },
];

export function SynthesisPanel({ data }: { data: SynthesisData }) {
    const [filter, setFilter] = useState<FilterMode>('all');
    const [expanded, setExpanded] = useState<Set<number>>(new Set());
    const [overrides, setOverrides] = useState<Record<string, Override>>({});
    const [editingId, setEditingId] = useState<number | null>(null);

    // Load existing overrides for this paper
    useEffect(() => {
        if (!data.paperKey) return;
        fetch(`/api/overrides/${encodeURIComponent(data.paperKey)}`)
            .then(r => r.json())
            .then(setOverrides)
            .catch(() => {});
    }, [data.paperKey]);

    const saveOverride = useCallback(async (citId: number, verdict: string, notes: string) => {
        await fetch('/api/override', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                paper_key: data.paperKey,
                citation_id: citId,
                verdict,
                notes,
            }),
        });
        setOverrides(prev => ({ ...prev, [String(citId)]: { verdict, notes } }));
        setEditingId(null);
    }, [data.paperKey]);

    const removeOverride = useCallback(async (citId: number) => {
        await fetch('/api/override', {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                paper_key: data.paperKey,
                citation_id: citId,
            }),
        });
        setOverrides(prev => {
            const next = { ...prev };
            delete next[String(citId)];
            return next;
        });
        setEditingId(null);
    }, [data.paperKey]);

    // Recompute counts with overrides applied
    const getEffectiveStatus = (c: CitationDetail) => {
        const ov = overrides[String(c.id)];
        if (ov) return ov.verdict;
        if (c.existence_status === 'not_found') return 'not_found';
        const v = c.verification?.verdict;
        if (v === 'supported') return 'supported';
        if (v === 'contradicted') return 'contradicted';
        return 'uncertain';
    };

    const counts = { supported: 0, contradicted: 0, uncertain: 0, not_found: 0 };
    for (const c of data.citations) {
        const s = getEffectiveStatus(c);
        if (s in counts) counts[s as keyof typeof counts]++;
    }

    const effectiveScore = counts.supported + counts.contradicted + counts.uncertain > 0
        ? Math.round((counts.supported / (counts.supported + counts.contradicted + counts.uncertain)) * 1000) / 10
        : 0;

    const verdict =
        effectiveScore >= 90 ? 'HIGH CONFIDENCE' :
            effectiveScore >= 70 ? 'REVIEW SUGGESTED' : 'NEEDS REVIEW';

    const verdictClass =
        effectiveScore >= 90 ? 'verdict-pass' :
            effectiveScore >= 70 ? 'verdict-caution' : 'verdict-fail';

    const filtered = data.citations.filter(c => {
        const s = getEffectiveStatus(c);
        if (filter === 'all') return true;
        return s === filter;
    });

    const toggle = (id: number) => {
        setExpanded(prev => {
            const next = new Set(prev);
            if (next.has(id)) next.delete(id);
            else next.add(id);
            return next;
        });
    };

    return (
        <div className="synthesis-overlay anim-fade-up">
            <div className="synthesis-card">
                <div className="synth-head rule-bottom">
                    <span className="label">Integrity Report</span>
                    <span className="synth-ts mono">{new Date().toLocaleTimeString()}</span>
                </div>

                <div className={`synth-verdict ${verdictClass}`}>
                    <div className="verdict-score mono">{effectiveScore}</div>
                    <div className="verdict-right">
                        <div className="verdict-label">Confidence Score</div>
                        <div className={`verdict-flag ${verdictClass}`}>{verdict}</div>
                    </div>
                </div>

                <div className="synth-citations rule-top">
                    <div className="label" style={{ padding: '0.6rem 0', borderBottom: '1px solid var(--rule)' }}>
                        Citation Audit
                        {Object.keys(overrides).length > 0 && (
                            <span className="override-count mono"> ({Object.keys(overrides).length} edited)</span>
                        )}
                    </div>
                    <div className="cit-grid cit-grid-5">
                        <button className={`cit-cell cit-cell-btn ${filter === 'all' ? 'cit-active' : ''}`} onClick={() => setFilter('all')}>
                            <div className="cit-num mono">{data.totalCitations}</div>
                            <div className="label">Total</div>
                        </button>
                        <button className={`cit-cell cit-cell-btn cit-ok ${filter === 'supported' ? 'cit-active' : ''}`} onClick={() => setFilter('supported')}>
                            <div className="cit-num mono">{counts.supported}</div>
                            <div className="label">Verified</div>
                        </button>
                        <button className={`cit-cell cit-cell-btn cit-warn ${filter === 'contradicted' ? 'cit-active' : ''}`} onClick={() => setFilter('contradicted')}>
                            <div className="cit-num mono">{counts.contradicted}</div>
                            <div className="label">Conflict</div>
                        </button>
                        <button className={`cit-cell cit-cell-btn cit-uncertain ${filter === 'uncertain' ? 'cit-active' : ''}`} onClick={() => setFilter('uncertain')}>
                            <div className="cit-num mono">{counts.uncertain}</div>
                            <div className="label">Unclear</div>
                        </button>
                        <button className={`cit-cell cit-cell-btn cit-notfound ${filter === 'not_found' ? 'cit-active' : ''}`} onClick={() => setFilter('not_found')}>
                            <div className="cit-num mono">{counts.not_found}</div>
                            <div className="label">Not Found</div>
                        </button>
                    </div>
                </div>

                <div className="synth-detail-list">
                    {filtered.length === 0 && (
                        <div className="detail-empty mono">No citations in this category.</div>
                    )}
                    {filtered.map(c => {
                        const isOpen = expanded.has(c.id);
                        const ov = overrides[String(c.id)];
                        const status = ov ? getOverrideStatusInfo(ov.verdict) : getStatusInfo(c);
                        return (
                            <div key={c.id} className={`detail-row ${status.cls}`}>
                                <button className="detail-header" onClick={() => toggle(c.id)}>
                                    <span className={`detail-badge ${status.cls}`}>
                                        {ov ? '\u270E' : status.badge}
                                    </span>
                                    <span className="detail-title mono">
                                        [{c.id}] {c.reference.title || c.reference.authors || 'Unknown'}
                                    </span>
                                    <span className="detail-chevron">{isOpen ? '\u25B4' : '\u25BE'}</span>
                                </button>
                                {isOpen && (
                                    <div className="detail-body">
                                        <div className="detail-field">
                                            <span className="label">Authors</span>
                                            <span>{c.reference.authors || 'Unknown'}</span>
                                        </div>
                                        {c.reference.year ? (
                                            <div className="detail-field">
                                                <span className="label">Year</span>
                                                <span>{c.reference.year}</span>
                                            </div>
                                        ) : null}
                                        <div className="detail-field">
                                            <span className="label">Claim</span>
                                            <span className="detail-claim">{c.claim}</span>
                                        </div>
                                        <div className="detail-field">
                                            <span className="label">Auto Status</span>
                                            <span className={getStatusInfo(c).cls}>{getStatusInfo(c).reason}</span>
                                        </div>
                                        {c.verification?.evidence && (
                                            <div className="detail-field">
                                                <span className="label">Evidence</span>
                                                <span className="detail-evidence">{c.verification.evidence}</span>
                                            </div>
                                        )}
                                        {c.verification && (
                                            <div className="detail-field">
                                                <span className="label">Method</span>
                                                <span className="mono">{c.verification.method} (confidence: {Math.round(c.verification.confidence * 100)}%)</span>
                                            </div>
                                        )}
                                        {c.source_found && (
                                            <div className="detail-field">
                                                <span className="label">Matched Source</span>
                                                <span>{c.source_found.title}</span>
                                            </div>
                                        )}

                                        {/* Manual override controls */}
                                        {ov && (
                                            <div className="override-banner">
                                                <span className="label">Your verdict: <strong>{ov.verdict}</strong></span>
                                                {ov.notes && <span className="override-notes">{ov.notes}</span>}
                                            </div>
                                        )}

                                        {editingId === c.id ? (
                                            <OverrideEditor
                                                current={ov}
                                                onSave={(v, n) => saveOverride(c.id, v, n)}
                                                onRemove={ov ? () => removeOverride(c.id) : undefined}
                                                onCancel={() => setEditingId(null)}
                                            />
                                        ) : (
                                            <button className="override-btn" onClick={() => setEditingId(c.id)}>
                                                {ov ? 'Edit Override' : 'Set Manual Verdict'}
                                            </button>
                                        )}
                                    </div>
                                )}
                            </div>
                        );
                    })}
                </div>

                <div className="synth-conclusion rule-top">
                    <div className="label" style={{ marginBottom: '0.5rem' }}>Summary</div>
                    <p className="conclusion-text">{data.conclusion}</p>
                </div>
            </div>
        </div>
    );
}


function OverrideEditor({ current, onSave, onRemove, onCancel }: {
    current?: Override;
    onSave: (verdict: string, notes: string) => void;
    onRemove?: () => void;
    onCancel: () => void;
}) {
    const [verdict, setVerdict] = useState(current?.verdict || 'supported');
    const [notes, setNotes] = useState(current?.notes || '');

    return (
        <div className="override-editor">
            <div className="override-row">
                {VERDICT_OPTIONS.map(opt => (
                    <button
                        key={opt.value}
                        className={`ov-choice ${opt.cls} ${verdict === opt.value ? 'ov-selected' : ''}`}
                        onClick={() => setVerdict(opt.value)}
                    >
                        {opt.label}
                    </button>
                ))}
            </div>
            <textarea
                className="override-notes-input mono"
                placeholder="Notes (optional)..."
                value={notes}
                onChange={e => setNotes(e.target.value)}
                rows={2}
            />
            <div className="override-actions">
                <button className="ov-save" onClick={() => onSave(verdict, notes)}>Save</button>
                {onRemove && <button className="ov-remove" onClick={onRemove}>Remove</button>}
                <button className="ov-cancel" onClick={onCancel}>Cancel</button>
            </div>
        </div>
    );
}


function getStatusInfo(c: CitationDetail) {
    if (c.existence_status === 'not_found') {
        return {
            badge: '?',
            cls: 'status-notfound',
            reason: 'Not found in academic databases. May still be legitimate.',
        };
    }
    const verdict = c.verification?.verdict;
    if (verdict === 'supported') {
        return { badge: '\u2713', cls: 'status-ok', reason: 'Claim aligns with source material.' };
    }
    if (verdict === 'contradicted') {
        return { badge: '!', cls: 'status-conflict', reason: 'Claim may conflict with source. Review recommended.' };
    }
    return { badge: '~', cls: 'status-uncertain', reason: 'Could not confidently verify. Review recommended.' };
}

function getOverrideStatusInfo(verdict: string) {
    if (verdict === 'supported') return { badge: '\u2713', cls: 'status-ok', reason: '' };
    if (verdict === 'contradicted') return { badge: '!', cls: 'status-conflict', reason: '' };
    if (verdict === 'not_found') return { badge: '?', cls: 'status-notfound', reason: '' };
    return { badge: '~', cls: 'status-uncertain', reason: '' };
}
