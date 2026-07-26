/**
 * The DataHub impact graph, drawn as a layered DAG.
 *
 * Hand-drawn SVG rather than a graph library. The layout is deterministic --
 * layer by longest path from the source, ordered within a layer by URN -- so
 * the picture is identical in every take of the demo, and the console installs
 * from five dependencies rather than fifty.
 *
 * Colour encodes the policy verdict, never the DataHub entity type: a judge is
 * looking for "what happens to this", and the artifact class is written on the
 * node anyway.
 */

import type { GraphEdge, GraphNode } from './types'

const NODE_W = 176
const NODE_H = 46
const GAP_X = 66
const GAP_Y = 20
const PAD = 22

interface Props {
  nodes: GraphNode[]
  edges: GraphEdge[]
  source: string
  selected: string | null
  onSelect: (urn: string) => void
}

interface Placed {
  node: GraphNode
  x: number
  y: number
}

/** Colour for one node, by what the policy decided about it. */
function palette(node: GraphNode): { fill: string; stroke: string } {
  if (node.is_source) return { fill: '#3d2a10', stroke: '#e3a008' }
  const actions = node.decision?.actions ?? []
  if (actions.includes('escalate')) return { fill: '#2c2140', stroke: '#bc8cff' }
  if (actions.includes('no_action')) return { fill: '#12281a', stroke: '#3fb950' }
  if (actions.length > 0) return { fill: '#3a1d1b', stroke: '#f85149' }
  return { fill: '#1c232d', stroke: '#3a4756' }
}

/**
 * Assign each node to a layer.
 *
 * Longest path from the source, not shortest: a node reachable by both a short
 * and a long route belongs to the right of everything it depends on, or edges
 * would draw backwards.
 */
function layerNodes(nodes: GraphNode[], edges: GraphEdge[], source: string): Map<string, number> {
  const depth = new Map<string, number>()
  nodes.forEach((n) => depth.set(n.urn, n.urn === source ? 0 : -1))

  // The graph is tiny and acyclic; relaxing |V| times is more than enough and
  // avoids needing a topological sort for a nine-node picture.
  for (let pass = 0; pass < nodes.length + 1; pass += 1) {
    for (const edge of edges) {
      const from = depth.get(edge.upstream) ?? -1
      if (from < 0) continue
      const current = depth.get(edge.downstream) ?? -1
      if (current < from + 1) depth.set(edge.downstream, from + 1)
    }
  }

  // Anything lineage never reached still has to appear, or the picture would
  // quietly omit an artifact the plan has an opinion about. Layer 1 puts it
  // beside the first-hop descendants; the dashed edge is what says the path
  // could not be resolved.
  for (const node of nodes) {
    if ((depth.get(node.urn) ?? -1) < 0) depth.set(node.urn, 1)
  }
  return depth
}

function layout(nodes: GraphNode[], edges: GraphEdge[], source: string): {
  placed: Placed[]
  width: number
  height: number
} {
  const depth = layerNodes(nodes, edges, source)
  const columns = new Map<number, GraphNode[]>()

  for (const node of [...nodes].sort((a, b) => a.urn.localeCompare(b.urn))) {
    const layer = depth.get(node.urn) ?? 0
    const bucket = columns.get(layer) ?? []
    bucket.push(node)
    columns.set(layer, bucket)
  }

  const layers = [...columns.keys()].sort((a, b) => a - b)
  const tallest = Math.max(...[...columns.values()].map((c) => c.length), 1)

  const placed: Placed[] = []
  for (const layer of layers) {
    const bucket = columns.get(layer) ?? []
    const columnHeight = bucket.length * NODE_H + (bucket.length - 1) * GAP_Y
    const fullHeight = tallest * NODE_H + (tallest - 1) * GAP_Y
    const top = PAD + (fullHeight - columnHeight) / 2

    bucket.forEach((node, index) => {
      placed.push({
        node,
        x: PAD + layer * (NODE_W + GAP_X),
        y: top + index * (NODE_H + GAP_Y),
      })
    })
  }

  const width = PAD * 2 + (Math.max(...layers, 0) + 1) * NODE_W + Math.max(layers.length - 1, 0) * GAP_X
  const height = PAD * 2 + tallest * NODE_H + (tallest - 1) * GAP_Y
  return { placed, width, height }
}

export function LineageGraph({ nodes, edges, source, selected, onSelect }: Props) {
  if (nodes.length === 0) {
    return <p className="muted">No lineage has been read yet.</p>
  }

  const { placed, width, height } = layout(nodes, edges, source)
  const at = new Map(placed.map((p) => [p.node.urn, p]))

  return (
    <>
      <div className="graph-scroll">
        <svg
          width={width}
          height={height}
          viewBox={`0 0 ${width} ${height}`}
          role="img"
          aria-label="Downstream lineage from the revoked source, coloured by policy verdict"
        >
          <defs>
            <marker id="arrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto">
              <path d="M0,0 L0,6 L7,3 z" fill="#3a4756" />
            </marker>
            <marker id="arrow-broken" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto">
              <path d="M0,0 L0,6 L7,3 z" fill="#bc8cff" />
            </marker>
          </defs>

          {edges.map((edge) => {
            const from = at.get(edge.upstream)
            const to = at.get(edge.downstream)
            if (!from || !to) return null

            const x1 = from.x + NODE_W
            const y1 = from.y + NODE_H / 2
            const x2 = to.x
            const y2 = to.y + NODE_H / 2
            const mid = (x1 + x2) / 2

            return (
              <path
                key={`${edge.upstream}->${edge.downstream}`}
                className={`graph-edge${edge.resolved ? '' : ' unresolved'}`}
                d={`M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2 - 8} ${y2}`}
                markerEnd={edge.resolved ? 'url(#arrow)' : 'url(#arrow-broken)'}
              />
            )
          })}

          {placed.map(({ node, x, y }) => {
            const colour = palette(node)
            const actions = node.decision?.actions ?? []
            const subtitle = actions.length
              ? actions.join(' + ')
              : (node.artifact_class ?? 'unclassified')

            return (
              <g
                key={node.urn}
                className={`graph-node${selected === node.urn ? ' selected' : ''}`}
                transform={`translate(${x}, ${y})`}
                onClick={() => onSelect(node.urn)}
                role="button"
                tabIndex={0}
                aria-label={`${node.label}: ${subtitle}`}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault()
                    onSelect(node.urn)
                  }
                }}
              >
                <rect
                  width={NODE_W}
                  height={NODE_H}
                  fill={colour.fill}
                  stroke={selected === node.urn ? '#58a6ff' : colour.stroke}
                />
                <text x={10} y={19}>
                  {node.label.replace(/^license\./, '')}
                </text>
                <text className="node-sub" x={10} y={34}>
                  {subtitle}
                </text>
                {node.revocation_status ? (
                  <circle cx={NODE_W - 11} cy={12} r={4} fill={
                    node.revocation_status === 'contained' ? '#3fb950'
                      : node.revocation_status === 'residual' ? '#f85149'
                      : '#bc8cff'
                  } />
                ) : null}
              </g>
            )
          })}
        </svg>
      </div>

      <div className="legend">
        <span><i className="swatch" style={{ background: '#e3a008' }} /> revoked source</span>
        <span><i className="swatch" style={{ background: '#f85149' }} /> containment required</span>
        <span><i className="swatch" style={{ background: '#3fb950' }} /> unaffected, no action</span>
        <span><i className="swatch" style={{ background: '#bc8cff' }} /> escalated</span>
        <span>dashed edge = DataHub could not resolve this lineage</span>
      </div>
    </>
  )
}
