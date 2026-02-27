import './KnowledgeGraph.css';

export interface NodeData {
    id: string;
    label: string;
    type: 'root' | 'agent' | 'finding' | 'contradiction';
    status: 'pending' | 'active' | 'complete' | 'error';
    x: number;
    y: number;
}

export interface EdgeData {
    source: string;
    target: string;
    active: boolean;
}

interface KnowledgeGraphProps {
    nodes: NodeData[];
    edges: EdgeData[];
}

export function KnowledgeGraph({ nodes, edges }: KnowledgeGraphProps) {
    const activeCount = nodes.filter(n => n.status === 'active').length;
    const errorCount = nodes.filter(n => n.type === 'contradiction').length;

    return (
        <div className="kg-container">
            {/* Header bar */}
            <div className="kg-head rule-bottom">
                <span className="label">Investigation Map</span>
                <div className="kg-status-chips">
                    <span className="chip chip-active mono">{activeCount} active</span>
                    {errorCount > 0 && (
                        <span className="chip chip-error mono">{errorCount} flagged</span>
                    )}
                </div>
            </div>

            {/* Graph canvas */}
            <div className="kg-canvas">
                {/* SVG edges */}
                <svg className="kg-svg" viewBox="0 0 100 100" preserveAspectRatio="none">
                    <defs>
                        <marker id="arrow" markerWidth="6" markerHeight="6" refX="3" refY="3" orient="auto">
                            <path d="M0,0 L0,6 L6,3 z" fill="var(--ink-faint)" />
                        </marker>
                    </defs>
                    {edges.map((edge, i) => {
                        const src = nodes.find(n => n.id === edge.source);
                        const tgt = nodes.find(n => n.id === edge.target);
                        if (!src || !tgt) return null;
                        return (
                            <line
                                key={i}
                                x1={src.x} y1={src.y}
                                x2={tgt.x} y2={tgt.y}
                                className={`kg-edge ${edge.active ? 'kg-edge-active' : ''}`}
                                markerEnd="url(#arrow)"
                            />
                        );
                    })}
                </svg>

                {/* Nodes */}
                {nodes.map(node => (
                    <div
                        key={node.id}
                        className={`kg-node kg-node-${node.type} kg-status-${node.status}`}
                        style={{ left: `${node.x}%`, top: `${node.y}%` }}
                    >
                        <div className="kg-node-label">
                            {node.label}
                        </div>
                        {node.status === 'active' && <div className="kg-node-pulse"></div>}
                    </div>
                ))}
            </div>

            {/* Legend */}
            <div className="kg-legend rule-top">
                <div className="legend-item"><span className="legend-dot dot-root"></span><span className="label">Root Query</span></div>
                <div className="legend-item"><span className="legend-dot dot-agent"></span><span className="label">Agent</span></div>
                <div className="legend-item"><span className="legend-dot dot-finding"></span><span className="label">Finding</span></div>
                <div className="legend-item"><span className="legend-dot dot-contradiction"></span><span className="label">Fabrication</span></div>
            </div>
        </div>
    );
}
