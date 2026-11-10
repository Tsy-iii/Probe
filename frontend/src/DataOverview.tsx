import { useState } from 'react'
import type { OrderDataset, PlatformDataset } from './api'

const labels: Record<string, string> = { pending: '待支付', paid: '已支付', cancelled: '已取消', queued: '待执行', running: '执行中', cancelling: '取消中', recovering: '恢复中', passed: '通过', failed: '测试失败', error: '执行异常', timed_out: '执行超时' }
const runStates = ['queued', 'running', 'cancelling', 'recovering', 'passed', 'failed', 'error', 'timed_out', 'cancelled']
interface Props {
  dataset: 'orders' | 'platform'; orders: OrderDataset | null; platform: PlatformDataset | null
  activeStatus: string | null; onStatus: (status: string) => void; onProduct: (id: number) => void
  onTable: (table: 'runs' | 'attempts' | 'workers') => void
}

// 订单状态用接口返回的全表计数；库存与平台状态只基于已加载记录，明确展示范围。
export default function DataOverview({ dataset, orders, platform, activeStatus, onStatus, onProduct, onTable }: Props) {
  const [expanded, setExpanded] = useState(() => window.innerWidth > 1100 && window.innerHeight >= 800)
  const data = dataset === 'orders' ? orders : platform
  if (!data) return null
  const counts: Record<string, number> = dataset === 'orders' && orders ? orders.status_counts : Object.fromEntries(runStates.map(state => [state, platform?.runs.filter(run => run.status === state).length ?? 0]))
  const states = dataset === 'orders' ? ['pending', 'paid', 'cancelled'] : runStates.filter(state => counts[state] > 0)
  const maxCount = Math.max(1, ...states.map(state => counts[state]))
  const products = orders?.products.filter(product => product.is_active !== false) ?? []
  const maxStock = Math.max(1, ...products.map(product => product.stock))
  return <section className={`data-insights ${expanded ? 'expanded' : ''}`} aria-label="数据概览">
    <button className="data-insights-toggle" aria-expanded={expanded} onClick={() => setExpanded(value => !value)}><strong>数据概览</strong><span>{dataset === 'orders' ? '演示订单与商品库存 · 自动测试数据独立准备' : '任务状态与执行关系 · 临时测试数据到报告中查看'}</span><span className="data-insights-fold">{expanded ? '收起' : '展开'} {expanded ? '⌃' : '⌄'}</span></button>
    {expanded && <div className={`data-chart-grid ${dataset === 'orders' ? 'order-charts' : ''}`}>
      <section className="data-chart" aria-label={dataset === 'orders' ? '演示订单状态分布' : '已加载任务状态分布'}>
        <header><h3>{dataset === 'orders' ? '订单状态分布' : '已加载任务状态'}</h3><span>{dataset === 'orders' ? `全表 ${orders?.counts.orders} 条` : `已加载 ${platform?.runs.length} / 全表 ${platform?.counts.runs}`}</span></header>
        <div className="data-chart-bars">{states.map(state => <button key={state} className={`data-chart-row ${activeStatus === state ? 'selected' : ''}`} aria-label={`筛选${labels[state]}${dataset === 'orders' ? '订单' : '任务'}`} aria-pressed={activeStatus === state} onClick={() => onStatus(state)}>
          <span>{labels[state]}</span><span className="data-chart-track"><span className={`data-chart-fill ${state}`} style={{ width: `${counts[state] / maxCount * 100}%` }} /></span><strong>{counts[state]}</strong>
        </button>)}</div>
        {states.length === 0 && <p className="data-chart-empty">当前没有已加载的任务记录。</p>}
        <p className="data-chart-note">点击状态筛选已加载记录；{dataset === 'orders' ? '计数来自当前数据库快照的全部订单。' : '包含故障演示任务，图表不表示全部用例的通过率。'}</p>
      </section>
      {dataset === 'orders' ? <section className="data-chart inventory-chart" aria-label="已加载商品库存">
        <header><h3>商品可售库存</h3><span>可见 {Math.min(2, products.length)} 个 · 共 {products.length} 个已加载在售商品</span></header>
        <div className="data-chart-bars inventory-scroll">{products.map(product => <button className="data-chart-row product" key={product.id} aria-label={`查看商品 ${product.name}`} onClick={() => onProduct(product.id)}>
          <span title={product.name}>{product.name}<small>商品 {product.id}</small></span><span className="data-chart-track"><span className="data-chart-fill stock" style={{ width: `${product.stock / maxStock * 100}%` }} /></span><strong>{product.stock}<small>件</small></strong>
        </button>)}</div>
        {products.length === 0 && <p className="data-chart-empty">当前没有已加载的商品。</p>}
        <p className="data-chart-note">列表独立滚动；点击查看商品详情，关联订单从详情进入。</p>
      </section> : <section className="data-chart" aria-label="任务尝试与 Worker 关系">
        <header><h3>任务 → 尝试 → Worker</h3><span>全表实际计数</span></header>
        <div className="data-execution-flow">{([['runs', '任务'], ['attempts', '执行尝试'], ['workers', '登记 Worker']] as const).map(([table, label]) => <button key={table} onClick={() => onTable(table)}><strong>{platform?.counts[table]}</strong><span>{label}</span></button>)}</div>
        <p className="data-chart-online">当前在线 <strong>{platform?.online_workers}</strong> 个 Worker · 在线状态来自最近心跳</p>
        <p className="data-chart-note">历史任务可能未记录尝试；异常恢复可能产生多次尝试。登记数量包含历史 Worker。</p>
      </section>}
    </div>}
  </section>
}
