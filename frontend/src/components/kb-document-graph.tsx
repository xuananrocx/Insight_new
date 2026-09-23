import { useMemo, useRef, useState } from 'react'
import { Network, AlertCircle } from 'lucide-react'
import { useQuery } from '@tanstack/react-query'

import { api, type KbGraphNode, type KbGraphEdge } from '@/lib/api'
import { Card } from '@/components/ui/card'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'

interface Props {
  kbId: string
}

interface SimNode {
  file_id: number
  name: string
  path?: string
  concept_count: number
  concepts: string[]
  x: number
  y: number
  vx: number
  vy: number
  radius: number
}

export function KbDocumentGraph({ kbId }: Props) {
  const [minShared, setMinShared] = useState(1)
  const [selectedNode, setSelectedNode] = useState<number | null>(null)
  const [hoveredNode, setHoveredNode] = useState<number | null>(null)

  const query = useQuery({
    queryKey: ['kb-graph', kbId, minShared],
    queryFn: () => api.kb.documentGraph(kbId, minShared),
    enabled: !!kbId,
  })

  const data = query.data
  const nodes = data?.nodes ?? []
  const edges = data?.edges ?? []

  // 力导向模拟（简化版：圆形布局 + 节点间距归一化）
  const layout = useForceLayout(nodes, edges)

  const selectedSharedEdges = useMemo(() => {
    if (selectedNode === null) return []
    return edges.filter((e) => e.source === selectedNode || e.target === selectedNode)
  }, [edges, selectedNode])

  const SVG_W = 600
  const SVG_H = 400

  if (query.isLoading) {
    return (
      <Card className="mb-6 p-5">
        <div className="py-8 text-center text-[12px] text-muted-foreground">加载关联图...</div>
      </Card>
    )
  }

  if (nodes.length === 0) {
    return (
      <Card className="mb-6 p-5">
        <Header
          conceptsCount={data?.concepts_count ?? 0}
          minShared={minShared}
          setMinShared={setMinShared}
        />
        <div className="px-5 py-8 text-center text-[12px] text-muted-foreground">
          <AlertCircle className="mx-auto mb-2 h-5 w-5 opacity-40" />
          还没有文档关联数据。
          <div className="mt-1 text-[10px]">
            需要先启用 AI 摘要并投喂文档，至少 2 个文档共享概念才能形成关联。
          </div>
        </div>
      </Card>
    )
  }

  return (
    <Card className="mb-6 overflow-hidden">
      <Header
        conceptsCount={data?.concepts_count ?? 0}
        minShared={minShared}
        setMinShared={setMinShared}
        nodesCount={nodes.length}
        edgesCount={edges.length}
      />
      <div className="grid grid-cols-1 md:grid-cols-[1fr_220px]">
        <div className="border-b bg-muted/10 p-3 md:border-b-0 md:border-r">
          <svg viewBox={`0 0 ${SVG_W} ${SVG_H}`} className="h-[300px] w-full">
            {/* 边 */}
            {layout.edges.map((e, i) => {
              const isHighlighted =
                selectedNode !== null && (e.source === selectedNode || e.target === selectedNode)
              return (
                <line
                  key={i}
                  x1={e.sourcePos.x}
                  y1={e.sourcePos.y}
                  x2={e.targetPos.x}
                  y2={e.targetPos.y}
                  stroke={isHighlighted ? 'currentColor' : '#71717a'}
                  strokeOpacity={isHighlighted ? 0.7 : selectedNode !== null ? 0.1 : 0.25}
                  strokeWidth={Math.max(1, Math.log2(e.shared_count + 1) * 1.5)}
                  className={isHighlighted ? 'text-primary' : ''}
                />
              )
            })}
            {/* 节点 */}
            {layout.nodes.map((n) => {
              const isSelected = selectedNode === n.file_id
              const isHovered = hoveredNode === n.file_id
              const isDimmed = selectedNode !== null && !isSelected && !layout.edges.some(
                (e) =>
                  (e.source === selectedNode && e.target === n.file_id) ||
                  (e.target === selectedNode && e.source === n.file_id),
              )
              return (
                <g
                  key={n.file_id}
                  transform={`translate(${n.x}, ${n.y})`}
                  style={{ cursor: 'pointer', opacity: isDimmed ? 0.35 : 1 }}
                  onClick={() => setSelectedNode(isSelected ? null : n.file_id)}
                  onMouseEnter={() => setHoveredNode(n.file_id)}
                  onMouseLeave={() => setHoveredNode(null)}
                >
                  <title>{n.path || n.name}</title>
                  <circle
                    r={n.radius}
                    fill={isSelected ? 'currentColor' : 'var(--background, #fff)'}
                    stroke={isSelected || isHovered ? 'currentColor' : '#71717a'}
                    strokeWidth={isSelected ? 2.5 : 1.5}
                    className={isSelected || isHovered ? 'text-primary' : ''}
                  />
                  <text
                    y={n.radius + 12}
                    textAnchor="middle"
                    fontSize="10"
                    fill="currentColor"
                    className="text-foreground"
                  >
                    {truncate(fileName(n.name), 20)}
                  </text>
                  <text
                    y={n.radius + 24}
                    textAnchor="middle"
                    fontSize="9"
                    fill="#71717a"
                  >
                    {n.concept_count} 概念
                  </text>
                </g>
              )
            })}
          </svg>
          <div className="mt-1 px-2 text-[10px] text-muted-foreground">
            点击节点查看共享概念 · 边的粗细 = 共享概念数
          </div>
        </div>

        {/* 侧栏：选中节点的详情 */}
        <div className="max-h-[340px] overflow-auto p-4">
          {selectedNode === null ? (
            <div className="text-[11px] text-muted-foreground">
              <div className="mb-2 font-medium text-foreground">操作提示</div>
              <ul className="list-disc space-y-1 pl-4 leading-relaxed">
                <li>点击节点查看详情</li>
                <li>节点大小 = 该文档的概念数</li>
                <li>边粗细 = 两文档共享概念数</li>
                <li>调整最小共享数过滤稀疏关联</li>
              </ul>
            </div>
          ) : (
            <SelectedNodeDetail
              node={layout.nodes.find((n) => n.file_id === selectedNode)!}
              edges={selectedSharedEdges}
              allNodesMap={Object.fromEntries(layout.nodes.map((n) => [n.file_id, n]))}
            />
          )}
        </div>
      </div>
    </Card>
  )
}

function Header({
  conceptsCount,
  minShared,
  setMinShared,
  nodesCount,
  edgesCount,
}: {
  conceptsCount: number
  minShared: number
  setMinShared: (v: number) => void
  nodesCount?: number
  edgesCount?: number
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 border-b px-5 py-3">
      <div className="flex items-center gap-2">
        <Network className="h-4 w-4 text-muted-foreground" />
        <span className="text-[14px] font-medium">文档关联图</span>
        <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
          {conceptsCount} 概念
          {nodesCount !== undefined ? ` · ${nodesCount} 文档 · ${edgesCount} 关联` : ''}
        </span>
      </div>
      <div className="flex items-center gap-1 text-[11px] text-muted-foreground">
        最小共享：
        <Select value={String(minShared)} onValueChange={(v) => setMinShared(Number(v))}>
          <SelectTrigger className="h-auto w-auto gap-1 px-1.5 py-0.5 text-[11px]">
            <SelectValue />
          </SelectTrigger>
          <SelectContent className="min-w-0">
            <SelectItem value="1" className="pl-3 pr-6">≥1</SelectItem>
            <SelectItem value="2" className="pl-3 pr-6">≥2</SelectItem>
            <SelectItem value="3" className="pl-3 pr-6">≥3</SelectItem>
            <SelectItem value="5" className="pl-3 pr-6">≥5</SelectItem>
          </SelectContent>
        </Select>
      </div>
    </div>
  )
}

function SelectedNodeDetail({
  node,
  edges,
  allNodesMap,
}: {
  node: SimNode
  edges: Array<{ source: number; target: number; shared_count: number; shared_concepts: string[] }>
  allNodesMap: Record<number, SimNode>
}) {
  return (
    <div className="space-y-3 text-[11px]">
      <div>
        <div className="break-all font-medium text-foreground" title={node.path || node.name}>{fileName(node.name)}</div>
        <div className="mt-0.5 text-muted-foreground">{node.concept_count} 个概念 · file_id #{node.file_id}</div>
      </div>

      <div>
        <div className="mb-1 font-medium text-foreground">本文档概念</div>
        <div className="flex flex-wrap gap-1">
          {node.concepts.map((name) => (
            <span key={name} className="rounded bg-muted/40 px-1.5 py-0.5 text-[10px]">
              {name}
            </span>
          ))}
        </div>
      </div>

      <div>
        <div className="mb-1 font-medium text-foreground">关联文档（{edges.length}）</div>
        {edges.length === 0 ? (
          <div className="text-muted-foreground">无关联（提高最小共享数过滤可发现稀疏关联）</div>
        ) : (
          <div className="space-y-1.5">
            {edges.map((e, i) => {
              const otherId = e.source === node.file_id ? e.target : e.source
              const other = allNodesMap[otherId]
              return (
                <div key={i} className="rounded bg-muted/30 p-2">
                  <div className="flex items-center justify-between">
                    <span className="min-w-0 break-all text-[11px] font-medium" title={other?.path || other?.name}>
                      {other ? fileName(other.name) : `file #${otherId}`}
                    </span>
                    <span className="rounded bg-primary/10 px-1.5 py-0.5 text-[9px] text-primary">
                      {e.shared_count} 共享
                    </span>
                  </div>
                  <div className="mt-1 flex flex-wrap gap-0.5">
                    {e.shared_concepts.slice(0, 6).map((c) => (
                      <span key={c} className="rounded bg-background px-1 py-0.5 text-[9px] text-muted-foreground">
                        {c}
                      </span>
                    ))}
                    {e.shared_concepts.length > 6 ? (
                      <span className="text-[9px] text-muted-foreground">
                        +{e.shared_concepts.length - 6}
                      </span>
                    ) : null}
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}

function truncate(s: string, n: number) {
  return s.length > n ? `${s.slice(0, n)}...` : s
}

/**
 * 简化力导向布局：圆形排列 + 边吸引（弹簧）+ 节点排斥（库仑）
 * 数据量小（< 50 节点），跑 200 步模拟即可稳定。
 */
function useForceLayout(
  rawNodes: KbGraphNode[],
  rawEdges: KbGraphEdge[],
): { nodes: SimNode[]; edges: Array<{ source: number; target: number; shared_count: number; shared_concepts: string[]; sourcePos: { x: number; y: number }; targetPos: { x: number; y: number } }> } {
  const cacheKey = useMemo(
    () => JSON.stringify({ n: rawNodes.map((n) => n.file_id), e: rawEdges.map((e) => [e.source, e.target, e.shared_count]) }),
    [rawNodes, rawEdges],
  )
  const cacheRef = useRef<Record<string, { nodes: SimNode[]; edges: any[] }>>({})

  if (!cacheRef.current[cacheKey]) {
    const W = 600
    const H = 400
    const cx = W / 2
    const cy = H / 2

    // 初始圆形布局
    const simNodes: SimNode[] = rawNodes.map((n, i) => {
      const angle = (i / Math.max(1, rawNodes.length)) * Math.PI * 2
      const radius = Math.min(W, H) * 0.35
      const r = Math.max(12, Math.min(28, 8 + n.concept_count * 1.5))
      return {
        ...n,
        x: cx + Math.cos(angle) * radius,
        y: cy + Math.sin(angle) * radius,
        vx: 0,
        vy: 0,
        radius: r,
      }
    })

    const posMap = new Map(simNodes.map((n) => [n.file_id, n]))

    // 模拟 250 步
    const REPULSION = 4000
    const ATTRACTION = 0.04
    const DAMPING = 0.85
    const CENTER_PULL = 0.005
    const MAX_SPEED = 4

    for (let step = 0; step < 250; step++) {
      // 节点间排斥
      for (let i = 0; i < simNodes.length; i++) {
        for (let j = i + 1; j < simNodes.length; j++) {
          const a = simNodes[i]
          const b = simNodes[j]
          const dx = b.x - a.x
          const dy = b.y - a.y
          const distSq = Math.max(50, dx * dx + dy * dy)
          const force = REPULSION / distSq
          const dist = Math.sqrt(distSq)
          const fx = (dx / dist) * force
          const fy = (dy / dist) * force
          a.vx -= fx
          a.vy -= fy
          b.vx += fx
          b.vy += fy
        }
      }
      // 边吸引
      for (const e of rawEdges) {
        const a = posMap.get(e.source)
        const b = posMap.get(e.target)
        if (!a || !b) continue
        const dx = b.x - a.x
        const dy = b.y - a.y
        const dist = Math.max(1, Math.sqrt(dx * dx + dy * dy))
        const targetDist = 120 - Math.min(60, e.shared_count * 8)
        const force = (dist - targetDist) * ATTRACTION
        const fx = (dx / dist) * force
        const fy = (dy / dist) * force
        a.vx += fx
        a.vy += fy
        b.vx -= fx
        b.vy -= fy
      }
      // 中心引力 + 阻尼 + 速度限制 + 应用位移
      for (const n of simNodes) {
        n.vx += (cx - n.x) * CENTER_PULL
        n.vy += (cy - n.y) * CENTER_PULL
        n.vx *= DAMPING
        n.vy *= DAMPING
        n.vx = Math.max(-MAX_SPEED, Math.min(MAX_SPEED, n.vx))
        n.vy = Math.max(-MAX_SPEED, Math.min(MAX_SPEED, n.vy))
        n.x += n.vx
        n.y += n.vy
        // 边界约束
        n.x = Math.max(40, Math.min(W - 40, n.x))
        n.y = Math.max(40, Math.min(H - 40, n.y))
      }
    }

    const simEdges = rawEdges.map((e) => ({
      source: e.source,
      target: e.target,
      shared_count: e.shared_count,
      shared_concepts: e.shared_concepts,
      sourcePos: { x: posMap.get(e.source)!.x, y: posMap.get(e.source)!.y },
      targetPos: { x: posMap.get(e.target)!.x, y: posMap.get(e.target)!.y },
    }))

    cacheRef.current[cacheKey] = { nodes: simNodes, edges: simEdges }
  }

  return cacheRef.current[cacheKey]
}

function fileName(path: string) { return path.replace(/\\/g, "/").split("/").pop() || path }
