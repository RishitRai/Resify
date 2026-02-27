import { useState } from 'react';
import type { FormEvent } from 'react';
import './ResearchHeader.css';

interface ResearchHeaderProps {
    onAnalyze: (query: string) => void;
    isAnalyzing: boolean;
    showCompactSearch: boolean;
}

export function ResearchHeader({ onAnalyze, isAnalyzing, showCompactSearch }: ResearchHeaderProps) {
    const [query, setQuery] = useState('');

    const handleSubmit = (e: FormEvent) => {
        e.preventDefault();
        if (query.trim() && !isAnalyzing) {
            onAnalyze(query);
        }
    };

    return (
        <header className={`masthead rule-bottom ${showCompactSearch ? 'masthead-with-search' : ''}`}>
            {/* Brand */}
            <div className="masthead-left">
                <div className="masthead-mark">
                    <span className="mark-ps">PS</span>
                </div>
                <div className="masthead-title-group">
                    <h1 className="masthead-title">PaperShield</h1>
                    <p className="masthead-sub label">Research Integrity Scanner — Vol. II, 2026</p>
                </div>
            </div>

            {/* Compact search — slides in from top when hero search scrolls away */}
            <form
                onSubmit={handleSubmit}
                className={`compact-form ${showCompactSearch ? 'compact-form-visible' : ''}`}
                aria-hidden={!showCompactSearch}
            >
                <input
                    type="text"
                    className="compact-input mono"
                    placeholder="arXiv URL or DOI…"
                    value={query}
                    onChange={e => setQuery(e.target.value)}
                    disabled={isAnalyzing || !showCompactSearch}
                    tabIndex={showCompactSearch ? 0 : -1}
                />
                <button
                    type="submit"
                    className="compact-btn"
                    disabled={isAnalyzing || !query.trim() || !showCompactSearch}
                >
                    {isAnalyzing ? '…' : 'Scan →'}
                </button>
            </form>
        </header>
    );
}
