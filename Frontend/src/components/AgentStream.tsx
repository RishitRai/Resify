import './AgentStream.css';
import { useEffect, useRef } from 'react';

export interface LogEvent {
    id: string;
    timestamp: string;
    agent: string;
    message: string;
    type: 'info' | 'success' | 'warning' | 'error';
}

const AGENT_ABBREVS: Record<string, string> = {
    'System': 'SYS',
    'Pipeline': 'PIP',
    'Fetcher': 'FET',
    'Citation Extractor': 'EXT',
    'Existence Checker': 'EXI',
    'Embedding Gate': 'EMB',
    'LLM Verifier': 'LLM',
    'Synthesizer': 'SYN',
};

interface AgentStreamProps {
    logs: LogEvent[];
}

export function AgentStream({ logs }: AgentStreamProps) {
    const endRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        endRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [logs]);

    return (
        <div className="stream-panel">
            <div className="stream-head rule-bottom">
                <span className="label">Agent Activity Log</span>
                <span className="stream-count mono">{logs.length} events</span>
            </div>

            <div className="stream-body">
                {logs.length === 0 && (
                    <div className="stream-empty">
                        <p className="stream-empty-text">Awaiting dispatch…</p>
                    </div>
                )}
                {logs.map((log, i) => (
                    <div
                        key={log.id}
                        className={`log-row log-${log.type}`}
                        style={{ animationDelay: `${i * 30}ms` }}
                    >
                        <div className="log-ts mono">{log.timestamp}</div>
                        <div className={`log-badge log-badge-${log.type}`}>
                            {AGENT_ABBREVS[log.agent] ?? log.agent.slice(0, 3).toUpperCase()}
                        </div>
                        <div className="log-msg">{log.message}</div>
                    </div>
                ))}
                {logs.length > 0 && (
                    <div className="log-cursor">
                        <span className="cursor-blink">▌</span>
                    </div>
                )}
                <div ref={endRef} />
            </div>
        </div>
    );
}
