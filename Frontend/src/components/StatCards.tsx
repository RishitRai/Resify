import { useEffect, useRef } from 'react';
import './StatCards.css';

const STATS = [
    {
        stat: '21%',
        headline: 'of all ICLR 2026 peer reviews were fully AI-generated',
        sub: 'Out of 75,800 reviews submitted — identified by GPTZero analysis',
        source: 'GPTZero / HowAIWorks, 2026',
        tag: 'Peer Review',
        severity: 'critical',
    },
    {
        stat: '100+',
        headline: 'hallucinated citations found in NeurIPS 2025 accepted papers',
        sub: 'Fabricated references embedded in 53 papers that beat 15,000+ competitors',
        source: 'ByteIota Research, NeurIPS 2025',
        tag: 'Citation Fraud',
        severity: 'critical',
    },
    {
        stat: '3×',
        headline: 'ICLR submission volume tripled in just two years',
        sub: 'From ~7,000 in 2024 to over 20,000 in 2026 — reviewers are overwhelmed',
        source: 'Science Magazine, 2026',
        tag: 'Submission Flood',
        severity: 'warning',
    },
    {
        stat: '53',
        headline: 'accepted NeurIPS papers contained AI-fabricated references',
        sub: 'Papers that passed full peer review despite containing non-existent citations',
        source: 'ByteIota / Science, 2025',
        tag: 'False Acceptance',
        severity: 'critical',
    },
    {
        stat: '★ 0',
        headline: 'tools exist that researchers can use before submitting',
        sub: 'GPTZero builds detection for institutions. Nobody built the tool for the author.',
        source: 'Market Gap Analysis',
        tag: 'The Gap',
        severity: 'opportunity',
    },
    {
        stat: '"…"',
        headline: '"The whole system is breaking down."',
        sub: '— Hany Farid, UC Berkeley computer scientist and computational forensics expert',
        source: 'Science Magazine, 2026',
        tag: 'Expert Verdict',
        severity: 'quote',
    },
];

export function StatCards() {
    const containerRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        const cards = containerRef.current?.querySelectorAll('.stat-card');
        if (!cards) return;

        const observer = new IntersectionObserver(
            entries => {
                entries.forEach(entry => {
                    if (entry.isIntersecting) {
                        entry.target.classList.add('card-visible');
                    }
                });
            },
            { threshold: 0.15, rootMargin: '0px 0px -40px 0px' }
        );

        cards.forEach(card => observer.observe(card));
        return () => observer.disconnect();
    }, []);

    return (
        <section className="stat-cards-section" ref={containerRef}>
            <div className="stat-cards-header rule-bottom">
                <span className="label">The Evidence</span>
                <span className="label" style={{ color: 'var(--ink-faint)' }}>
                    Why peer review is in crisis — and why nothing is fixed yet
                </span>
            </div>

            <div className="stat-cards-grid">
                {STATS.map((item, i) => (
                    <div
                        key={i}
                        className={`stat-card sev-${item.severity}`}
                        style={{ transitionDelay: `${(i % 3) * 60}ms` }}
                    >
                        <div className="card-tag label">{item.tag}</div>
                        <div className="card-stat">{item.stat}</div>
                        <p className="card-headline">{item.headline}</p>
                        <p className="card-sub">{item.sub}</p>
                        <div className="card-source rule-top">
                            <span className="mono">{item.source}</span>
                        </div>
                    </div>
                ))}
            </div>
        </section>
    );
}
