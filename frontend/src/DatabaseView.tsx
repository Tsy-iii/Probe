import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react'
import { api, type DataOrder, type DataPayment, type DataProduct, type DataRun, type DataWorker, type OrderDataset, type PlatformDataset, type Attempt, type Suite } from './api'
import DataDrawer from './DataDrawer'
import DataOverview from './DataOverview'
import ProductEditor from './ProductEditor'
import ProductStateAction from './ProductStateAction'
import DemoOrderAction from './DemoOrderAction'
import type { DemoOperation } from './demoApi'

const date = (value: string | null) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'
const short = (value: string) => value.length > 15 ? `${value.slice(0, 8)}…` : value
const statusNames: Record<string, string> = { pending: '待支付', paid: '已支付', cancelled: '已取消', queued: '待执行', running: '执行中', cancelling: '取消中', recovering: '恢复中', passed: '通过', failed: '测试失败', error: '执行异常', timed_out: '超时', interrupted: 'Worker 中断' }
function money(cents: string) { const value = BigInt(cents); return `${value / 100n}.${(value % 100n).toString().padStart(2, '0')} 元` }
function Id({ value }: { value: string }) {
  const label = value.length > 18 ? `${value.slice(0, 10)}…${value.slice(-4)}` : value
  return <code className="data-id-label" title={value}>{label}</code>
}
function State({ value }: { value: string }) { return <span className={`data-state ${value}`}>{statusNames[value] ?? value}</span> }
function Fields({ rows }: { rows: [string, ReactNode][] }) { return <dl className="inspector-fields">{rows.map(([name, value]) => <div className="data-field-pair" key={name}><dt>{name}</dt><dd>{value}</dd></div>)}</dl> }

type TableName = 'products' | 'orders' | 'payment_events' | 'runs' | 'attempts' | 'workers'
type DataAttempt = Attempt & { run_id: string }
type DataRow = DataProduct | DataOrder | DataPayment | DataRun | DataAttempt | DataWorker
const tableNames: Record<TableName, [string, string]> = { products: ['商品', 'products'], orders: ['订单', 'orders'], payment_events: ['支付事件', 'payment_events'], runs: ['任务', 'test_runs'], attempts: ['尝试', 'run_attempts'], workers: ['Worker', 'workers'] }
interface Column<T> { name: string; width?: number | string; align?: 'right'; cell: (row: T) => ReactNode }
function Table<T>({ name, rows, columns, rowId, selected, onSelect, empty }: { name: string; rows: T[]; columns: Column<T>[]; rowId: (row: T) => string; selected?: string | null; onSelect: (row: T) => void; empty: string }) {
  return <div className="data-table-scroll">{rows.length ? <table className="data-table" aria-label={name}><thead><tr>{columns.map(column => <th key={column.name} style={{ width: column.width, textAlign: column.align }}>{column.name}</th>)}</tr></thead><tbody>{rows.map(row => <tr key={rowId(row)} data-row-id={rowId(row)} className={`selectable ${selected === rowId(row) ? 'selected-row' : ''}`} tabIndex={0} aria-selected={selected === rowId(row)} onClick={() => onSelect(row)} onKeyDown={event => { if (['Enter', ' '].includes(event.key)) { event.preventDefault(); onSelect(row) } }}>{columns.map(column => <td key={column.name} style={{ textAlign: column.align }}>{column.cell(row)}</td>)}</tr>)}</tbody></table> : <div className="data-empty">{empty}</div>}</div>
}

export default function DatabaseView({ onInspectRun, suites = [] }: { onInspectRun: (id: string) => void; suites?: Suite[] }) {
  const [dataset, setDataset] = useState<'orders' | 'platform'>('orders')
  const [orders, setOrders] = useState<OrderDataset | null>(null)
  const [platform, setPlatform] = useState<PlatformDataset | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [table, setTable] = useState<TableName>('products')
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState<string | null>(null)
  const [productId, setProductId] = useState<number | null>(null)
  const [orderId, setOrderId] = useState<string | null>(null)
  const [runId, setRunId] = useState<string | null>(null)
  const [workerId, setWorkerId] = useState<string | null>(null)
  const [record, setRecord] = useState<{ table: TableName; id: string } | null>(null)
  const [drawer, setDrawer] = useState<'guide' | 'record' | null>(null)
  const [copied, setCopied] = useState('')
  const [editor, setEditor] = useState<DataProduct | null>(null)
  const [createOpen, setCreateOpen] = useState(false)
  const [productAction, setProductAction] = useState<{ product: DataProduct; action: 'delete' | 'restore' } | null>(null)
  const [productVisibility, setProductVisibility] = useState<'active' | 'deleted' | 'all'>('active')
  const [focusProduct, setFocusProduct] = useState<number | null>(null)
  const [editNotice, setEditNotice] = useState('')
  const [demoAction, setDemoAction] = useState<{ operation: DemoOperation; product?: DataProduct; order?: DataOrder } | null>(null)
  useEffect(() => { if (!editNotice) return; const timer = window.setTimeout(() => setEditNotice(''), 8000); return () => window.clearTimeout(timer) }, [editNotice])
  const pending = useRef<AbortController | null>(null)
  const revealProduct = useRef<number | null>(null)
  const refresh = useCallback(async () => {
    pending.current?.abort()
    const request = new AbortController(); pending.current = request
    setLoading(true); setError('')
    try {
      if (dataset === 'orders') { const result = await api<OrderDataset>(`/data/orders${focusProduct === null ? '' : `?focus_product_id=${focusProduct}`}`, { signal: request.signal }); if (!request.signal.aborted) setOrders(result) }
      else { const result = await api<PlatformDataset>('/data/platform', { signal: request.signal }); if (!request.signal.aborted) setPlatform(result) }
    } catch (exception) { if (!request.signal.aborted) setError(exception instanceof Error ? exception.message : '读取数据失败') }
    finally { if (!request.signal.aborted) setLoading(false) }
  }, [dataset, focusProduct])
  useEffect(() => { void refresh(); return () => pending.current?.abort() }, [refresh])
  function changeDataset(value: 'orders' | 'platform') { if (dataset === value) return; setDataset(value); setTable(value === 'orders' ? 'products' : 'runs'); clearRelations(); setSearch(''); setRecord(null); setDrawer(null) }
  function clearRelations() { setProductId(null); setOrderId(null); setRunId(null); setWorkerId(null); setStatusFilter(null); setFocusProduct(null) }
  // 选中记录只控制详情；只有明确的关联入口才改变表格和筛选。
  function navigateTable(value: TableName, relation: { product?: number; order?: string; run?: string; worker?: string } = {}) {
    clearRelations(); setTable(value); setSearch(''); setDrawer(null); setRecord(null)
    setProductId(relation.product ?? null); setOrderId(relation.order ?? null)
    setRunId(relation.run ?? null); setWorkerId(relation.worker ?? null)
    if (value === 'products' && relation.product !== undefined) setProductVisibility('all')
  }
  function showTable(value: TableName) { if (table !== value) navigateTable(value) }
  function filterStatus(value: string) {
    if (statusFilter === value) { clearRelations(); return }
    clearRelations(); setTable(dataset === 'orders' ? 'orders' : 'runs'); setSearch(''); setDrawer(null); setRecord(null); setStatusFilter(value)
  }
  function inspect(value: TableName, id: string) { setRecord({ table: value, id }); setCopied(''); setDrawer('record') }
  const selectedId = (value: TableName) => record?.table === value ? record.id : null
  const data = dataset === 'orders' ? orders : platform
  const source = data?.source
  const editing = dataset === 'orders' ? orders?.product_editing : undefined
  function editProduct(product: DataProduct) { if (editing?.enabled && product.is_active !== false) { setEditNotice(''); setDrawer(null); setEditor(product) } }
  function openDemo(operation: DemoOperation, row: DataProduct | DataOrder) {
    if (!orders?.demo_orders?.enabled) return
    setEditNotice(''); setDrawer(null)
    setDemoAction(operation === 'create' ? { operation, product: row as DataProduct } : { operation, order: row as DataOrder })
  }
  function finishProductChange(product: DataProduct, action: 'create' | 'edit' | 'delete' | 'restore') {
    setEditor(null); setCreateOpen(false); setProductAction(null); setDrawer(null)
    clearRelations(); setSearch(''); setTable('products'); setProductVisibility(action === 'delete' ? 'deleted' : 'active')
    setRecord({ table: 'products', id: String(product.id) })
    revealProduct.current = product.id
    const text = { create: '已新增', edit: '已保存', delete: '已删除，可恢复', restore: '已恢复' }[action]
    setEditNotice(`商品 ${product.name} ${text}，正在刷新快照。`)
    // 普通新增保留完整列表；只有行数上限可能隐藏新商品时才使用明确的定位视图。
    const needsFocus = (action === 'create' || focusProduct !== null) && (orders?.counts.products ?? 0) >= (source?.row_limit ?? 50)
    if (needsFocus) setFocusProduct(product.id)
    void refresh()
  }
  const suiteName = (id: string) => suites.find(suite => suite.id === id)?.name ?? id
  const rowsByTable: Record<TableName, DataRow[]> = { products: orders?.products ?? [], orders: orders?.orders ?? [], payment_events: orders?.payment_events ?? [], runs: platform?.runs ?? [], attempts: platform?.attempts ?? [], workers: platform?.workers ?? [] }
  const keyOf = (kind: TableName, row: DataRow) => kind === 'payment_events' ? (row as DataPayment).event_id : String((row as { id: string | number }).id)
  const current = record ? rowsByTable[record.table].find(row => keyOf(record.table, row) === record.id) : undefined
  const relatedOrders = orders?.orders.filter(row => (productId === null || row.product_id === productId) && (!orderId || row.id === orderId)) ?? []
  const relatedEvents = orders?.payment_events.filter(row => orderId ? row.order_id === orderId : productId === null || row.product_id === productId) ?? []
  const relatedAttempts = platform?.attempts.filter(row => (!runId || row.run_id === runId) && (!workerId || row.worker_id === workerId)) ?? []
  const relatedRuns = platform?.runs.filter(row => !runId || row.id === runId) ?? []
  function matches<T extends DataRow>(rows: T[]): T[] { const term = search.trim().toLowerCase(); return rows.filter(row => (!statusFilter || ('status' in row && row.status === statusFilter)) && [JSON.stringify(row), 'status' in row ? statusNames[row.status] : '', 'suite_id' in row ? suiteName(row.suite_id) : ''].some(value => value?.toLowerCase().includes(term))) }
  const countFor = (kind: TableName) => dataset === 'orders' ? orders?.counts[kind as keyof OrderDataset['counts']] : platform?.counts[kind as keyof PlatformDataset['counts']]
  const filteredProducts = (orders?.products ?? []).filter(row => (productId === null || row.id === productId) && (productVisibility === 'all' || (productVisibility === 'active' ? row.is_active !== false : row.is_active === false)))
  const visibleRows = matches(table === 'products' ? filteredProducts : table === 'orders' ? relatedOrders : table === 'payment_events' ? relatedEvents : table === 'runs' ? relatedRuns : table === 'attempts' ? relatedAttempts : rowsByTable[table])
  const relationActive = productId !== null || orderId !== null || runId !== null || workerId !== null || statusFilter !== null || focusProduct !== null
  useEffect(() => {
    if (table !== 'products' || revealProduct.current === null) return
    const row = document.querySelector<HTMLElement>(`[aria-label="商品记录"] [data-row-id="${revealProduct.current}"]`)
    const viewport = row?.closest<HTMLElement>('.data-table-scroll')
    if (!row || !viewport) return
    const box = row.getBoundingClientRect(), frame = viewport.getBoundingClientRect()
    if (box.bottom > frame.bottom) viewport.scrollTop += box.bottom - frame.bottom
    else if (box.top < frame.top) viewport.scrollTop -= frame.top - box.top
    row.focus({ preventScroll: true }); revealProduct.current = null
  }, [orders, table, productVisibility])

  const productColumns: Column<DataProduct>[] = [{ name: '商品 ID', width: '12%', cell: row => <code>{row.id}</code> }, { name: '名称', width: '30%', cell: row => <strong>{row.name}</strong> }, { name: '当前单价', width: '24%', cell: row => <span>{money(row.price_cents)}<small>{row.price_cents} 分</small></span> }, { name: '剩余库存', width: '16%', cell: row => <strong className="data-number">{row.stock}</strong> }, { name: '状态', width: '18%', cell: row => <span className={`data-state ${row.is_active === false ? 'cancelled' : 'paid'}`}>{row.is_active === false ? '已删除' : '在售'}</span> }]
  const orderColumns: Column<DataOrder>[] = [{ name: '订单 ID', width: 150, cell: row => <Id value={row.id} /> }, { name: '商品', width: 190, cell: row => <span>{row.product_name}<small>商品 {row.product_id}</small></span> }, { name: '数量', width: 70, align: 'right', cell: row => row.quantity }, { name: '订单金额', width: 150, align: 'right', cell: row => <span>{money(row.total_amount_cents)}<small>{row.total_amount_cents} 分</small></span> }, { name: '状态', width: 110, cell: row => <State value={row.status} /> }, { name: '支付事件', width: 95, align: 'right', cell: row => row.payment_event_count }, { name: '创建时间', width: 185, cell: row => date(row.created_at) }]
  const eventColumns: Column<DataPayment>[] = [{ name: '事件 ID', width: 190, cell: row => <Id value={row.event_id} /> }, { name: '关联订单', width: 160, cell: row => <Id value={row.order_id} /> }, { name: '支付金额', width: 170, align: 'right', cell: row => <span>{money(row.amount_cents)}<small>{row.amount_cents} 分</small></span> }, { name: '订单状态', width: 130, cell: row => <State value={row.order_status} /> }, { name: '登记时间', width: 190, cell: row => date(row.created_at) }]
  const runColumns: Column<DataRun>[] = [{ name: '任务 ID', width: 150, cell: row => <Id value={row.id} /> }, { name: '测试套件', width: 210, cell: row => <span><strong>{suiteName(row.suite_id)}</strong><small><code>{row.suite_id}</code></small></span> }, { name: '状态', width: 110, cell: row => <State value={row.status} /> }, { name: '执行尝试', width: 100, align: 'right', cell: row => row.attempt_count }, { name: '实际测试结果', width: 150, align: 'right', cell: row => row.total_count === null ? '尚无计数' : `${row.passed_count}/${row.total_count} 通过` }, { name: '提交时间', width: 185, cell: row => date(row.created_at) }]
  const attemptColumns: Column<DataAttempt>[] = [{ name: '尝试 ID', width: 145, cell: row => <Id value={row.id} /> }, { name: '所属任务', width: 145, cell: row => <Id value={row.run_id} /> }, { name: '次数', width: 65, align: 'right', cell: row => row.number }, { name: '状态', width: 110, cell: row => <State value={row.status} /> }, { name: 'Worker', width: 140, cell: row => <Id value={row.worker_id} /> }, { name: '测试 schema', width: 330, cell: row => <code>{row.isolation_schema ?? '无需订单测试库'}</code> }, { name: '资源清理', width: 155, cell: row => row.cleanup_done ? '进程 / 数据已清理' : '尚未完成' }, { name: '停止原因', width: 150, cell: row => row.stop_reason ?? '—' }]
  const workerColumns: Column<DataWorker>[] = [{ name: 'Worker ID', width: 190, cell: row => <Id value={row.id} /> }, { name: '进程 PID', width: 115, align: 'right', cell: row => <code>{row.process_id}</code> }, { name: '当前状态', width: 220, cell: row => <span className={`data-state ${row.online ? 'paid' : 'cancelled'}`}>{row.online ? '近期心跳在线' : '离线 / 心跳已过期'}</span> }, { name: '最后报告状态', width: 150, cell: row => <code>{row.last_status}</code> }, { name: '最后心跳', width: 190, cell: row => date(row.heartbeat_at) }]

  function renderTable() {
    if (table === 'products') return <Table name="商品记录" rows={matches(filteredProducts)} columns={productColumns} rowId={row => String(row.id)} selected={selectedId('products')} onSelect={row => inspect('products', String(row.id))} empty="没有匹配的已加载商品。可清除搜索或筛选，也可以新增演示商品。" />
    if (table === 'orders') return <Table name="订单记录" rows={matches(relatedOrders)} columns={orderColumns} rowId={row => row.id} selected={selectedId('orders')} onSelect={row => inspect('orders', row.id)} empty="当前搜索或关联筛选没有加载到订单。" />
    if (table === 'payment_events') return <Table name="支付事件记录" rows={matches(relatedEvents)} columns={eventColumns} rowId={row => row.event_id} selected={selectedId('payment_events')} onSelect={row => inspect('payment_events', row.event_id)} empty="当前没有加载到成功支付事件；测试中的事件请到报告快照查看。" />
    if (table === 'runs') return <Table name="任务记录" rows={matches(relatedRuns)} columns={runColumns} rowId={row => row.id} selected={selectedId('runs')} onSelect={row => inspect('runs', row.id)} empty="没有匹配的已加载任务。可清除搜索或筛选，也可以到测试工作台提交套件。" />
    if (table === 'attempts') return <Table name="执行尝试记录" rows={matches(relatedAttempts)} columns={attemptColumns} rowId={row => row.id} selected={selectedId('attempts')} onSelect={row => inspect('attempts', row.id)} empty="当前没有加载到关联尝试；未执行任务或旧记录可以没有尝试。" />
    return <Table name="Worker 记录" rows={matches(platform?.workers ?? [])} columns={workerColumns} rowId={row => row.id} selected={selectedId('workers')} onSelect={row => inspect('workers', row.id)} empty="当前没有匹配的 Worker 注册记录。" />
  }

  function recordInfo() {
    if (!record || !current) return <p className="inspector-hint">该记录未在当前快照中加载，不能据此判断数据库中不存在。请刷新数据后再查看。</p>
    if (record.table === 'products') {
      const row = current as DataProduct
      return <>
        <Fields rows={[["商品 ID", row.id], ["名称", row.name], ["状态", <span className={`data-state ${row.is_active === false ? 'cancelled' : 'paid'}`}>{row.is_active === false ? '已删除' : '在售'}</span>], ["单价", money(row.price_cents)], ["整数分", <code>{row.price_cents}</code>], ["当前库存", row.stock]]} />
        {editing?.enabled && <section className="product-edit-entry">
          <h3>商品操作</h3>
          <p>{row.is_active === false ? '恢复后可再次下单，保留当前价格、库存和历史记录。' : '价格用于后续订单；库存调整不会修改已有订单金额或支付事件。'}</p>
          <div className="product-detail-actions">
            {orders?.demo_orders?.enabled && row.is_active !== false && <button className="demo-primary-action" onClick={() => openDemo('create', row)}>创建订单</button>}
            {row.is_active !== false && <button onClick={() => editProduct(row)}>编辑价格与库存</button>}
            {(row.is_active === false ? editing.can_restore : editing.can_delete) && <button className={row.is_active === false ? '' : 'product-delete-button'} onClick={() => { setEditNotice(''); setDrawer(null); setProductAction({ product: row, action: row.is_active === false ? 'restore' : 'delete' }) }}>{row.is_active === false ? '恢复商品' : '删除商品（可恢复）'}</button>}
          </div>
        </section>}
        <section className="data-drawer-relation"><h3>关联订单</h3><p>当前快照加载了 {orders?.orders.filter(order => order.product_id === row.id).length ?? 0} 条关联订单。点击下方按钮后，订单表将按这个商品筛选。</p><button onClick={() => navigateTable('orders', { product: row.id })}>查看关联订单 →</button></section>
      </>
    }
    if (record.table === 'orders') {
      const row = current as DataOrder
      return <>
        <Fields rows={[["订单 ID", <code>{row.id}</code>], ["商品", row.product_name], ["状态", <State value={row.status} />], ["数量", row.quantity], ["总金额", money(row.total_amount_cents)], ["整数分", <code>{row.total_amount_cents}</code>], ["创建时间", date(row.created_at)], ["更新时间", date(row.updated_at)]]} />
        {orders?.demo_orders?.enabled && <section className="demo-detail-actions"><h3>订单操作</h3>{row.status === 'pending' ? <><p>通过订单 API 模拟支付或取消，并查看真实请求和响应。</p><div className="product-detail-actions"><button className="demo-primary-action" onClick={() => openDemo('pay', row)}>模拟支付</button><button onClick={() => openDemo('cancel', row)}>取消订单</button></div></> : <p>{row.status === 'paid' ? '订单已支付，暂不支持取消。支付回调重放可在本次支付操作窗口中体验。' : '订单已取消，不能再支付。重复取消可在本次取消操作窗口中体验。'}</p>}</section>}
        <section className="data-drawer-relation"><h3>关联记录</h3><p>商品 {row.product_id} · 当前库存 {orders?.products.find(product => product.id === row.product_id)?.stock ?? '未加载'}。成功支付事件共 {row.payment_event_count} 条，当前加载 {orders?.payment_events.filter(event => event.order_id === row.id).length ?? 0} 条。</p><div><button onClick={() => navigateTable('products', { product: row.product_id })}>查看关联商品 →</button><button onClick={() => navigateTable('payment_events', { order: row.id })}>查看支付事件 →</button></div></section>
      </>
    }
    if (record.table === 'payment_events') {
      const row = current as DataPayment
      return <><Fields rows={[["事件 ID", <code>{row.event_id}</code>], ["订单 ID", <code>{row.order_id}</code>], ["支付金额", money(row.amount_cents)], ["整数分", <code>{row.amount_cents}</code>], ["订单状态", <State value={row.order_status} />], ["登记时间", date(row.created_at)]]} /><section className="data-drawer-relation"><h3>关联订单</h3><p>同一 event_id 重复回调不会重复产生业务变更。</p><button onClick={() => navigateTable('orders', { order: row.order_id })}>查看关联订单 →</button></section></>
    }
    if (record.table === 'runs') {
      const row = current as DataRun
      return <><Fields rows={[["任务 ID", <code>{row.id}</code>], ["套件", suiteName(row.suite_id)], ["状态", <State value={row.status} />], ["尝试次数", row.attempt_count], ["实际通过", row.total_count === null ? '尚无报告计数' : `${row.passed_count}/${row.total_count}`], ["每次超时", `${row.timeout_seconds} s`], ["提交时间", date(row.created_at)]]} /><section className="data-drawer-relation"><h3>执行记录</h3><p>当前加载 {platform?.attempts.filter(attempt => attempt.run_id === row.id).length ?? 0} 条尝试。取消测试任务不等于取消业务订单。</p><div><button onClick={() => navigateTable('attempts', { run: row.id })}>查看执行尝试 →</button><button onClick={() => onInspectRun(row.id)}>打开报告与日志 →</button></div></section></>
    }
    if (record.table === 'attempts') {
      const row = current as DataAttempt
      return <><Fields rows={[["尝试 ID", <code>{row.id}</code>], ["所属任务", <code>{row.run_id}</code>], ["次数", row.number], ["状态", <State value={row.status} />], ["Worker", <code>{row.worker_id}</code>], ["进程 PID", row.process_id ?? '—'], ["测试 schema", <code>{row.isolation_schema ?? '无需订单测试库'}</code>], ["资源清理", row.cleanup_done ? '进程 / 数据已清理' : '尚未完成'], ["停止原因", row.stop_reason ?? '—']]} /><section className="data-drawer-relation"><h3>所属任务</h3><div><button onClick={() => navigateTable('runs', { run: row.run_id })}>查看所属任务 →</button><button onClick={() => onInspectRun(row.run_id)}>打开报告与日志 →</button></div></section></>
    }
    const row = current as DataWorker
    return <><Fields rows={[["Worker ID", <code>{row.id}</code>], ["进程 PID", row.process_id], ["当前在线", row.online ? '近期心跳在线' : '离线 / 心跳已过期'], ["最后报告状态", row.last_status], ["最后心跳", date(row.heartbeat_at)]]} /><section className="data-drawer-relation"><h3>关联执行尝试</h3><p>当前加载 {platform?.attempts.filter(attempt => attempt.worker_id === row.id).length ?? 0} 条关联尝试。历史记录数不等于当前在线数。</p><button onClick={() => navigateTable('attempts', { worker: row.id })}>查看关联尝试 →</button></section></>
  }

  return <div className="database-view data-workbench">
    <header className="page-header"><div><div className="eyebrow">PROBE / DATA EXPLORER</div><h1>数据浏览</h1></div><div className="data-header-actions"><button className="inspect-run-button" onClick={() => setDrawer('guide')}>数据说明</button><button className="data-refresh" onClick={() => void refresh()} disabled={loading}>{loading ? '读取中…' : '刷新数据'}</button></div></header>
    <section className="data-overview"><div className="data-dataset-tabs" role="group" aria-label="数据来源"><button className={dataset === 'orders' ? 'current' : ''} onClick={() => changeDataset('orders')} aria-pressed={dataset === 'orders'}>订单演示数据</button><button className={dataset === 'platform' ? 'current' : ''} onClick={() => changeDataset('platform')} aria-pressed={dataset === 'platform'}>平台执行记录</button></div>{dataset === 'orders' && orders && <section className="data-stats"><div><span>商品</span><strong>{orders.counts.products}</strong></div><div><span>订单</span><strong>{orders.counts.orders}</strong></div><div><span>支付事件</span><strong>{orders.counts.payment_events}</strong></div></section>}{dataset === 'platform' && platform && <section className="data-stats"><div><span>任务</span><strong>{platform.counts.runs}</strong></div><div><span>执行尝试</span><strong>{platform.counts.attempts}</strong></div><div><span>Worker 历史</span><strong>{platform.counts.workers}</strong><small>{platform.online_workers} 在线</small></div></section>}</section>
    <div className="data-source-meta"><span className="data-readonly">{editing?.enabled ? '查询快照 · 商品可编辑' : '只读快照'}</span>{source ? <><span><code>{source.database}</code> / <code>{source.schema}</code></span><span title={date(source.captured_at)}>读取于 {new Date(source.captured_at).toLocaleTimeString('zh-CN', { hour12: false })}</span></> : <span>{loading ? '正在读取数据来源…' : '数据来源尚未读取'}</span>}<span className="data-scope-caption">{dataset === 'orders' ? '手动演示业务库；自动测试数据在报告快照中查看。' : '测试执行记录；Worker 在线状态根据最近心跳计算。'}</span></div>
    {error && <div className="alert" role="alert">{error}。{data ? '下方保留最近成功读取的快照。' : '可以切换其他数据来源查看。'}</div>}
    <DataOverview dataset={dataset} orders={orders} platform={platform} activeStatus={statusFilter} onStatus={filterStatus} onProduct={id => inspect('products', String(id))} onTable={value => navigateTable(value)} />
    <section className="data-card data-grid-card"><div className="section-heading"><div><h2>{dataset === 'orders' ? '业务数据表' : '平台执行数据表'}</h2><span className="pane-label">{tableNames[table][1]}</span></div><div className="data-record-actions">{editing?.can_create && <button className="data-edit-trigger" onClick={() => { setEditNotice(''); setDrawer(null); setEditor(null); setCreateOpen(true) }}>新增商品</button>}{editing?.enabled && table === 'products' && <button className="data-edit-trigger" disabled={!current || record?.table !== 'products' || !visibleRows.includes(current) || (current as DataProduct).is_active === false || loading} onClick={() => { if (current && record?.table === 'products') editProduct(current as DataProduct) }}>编辑选中商品</button>}<button className="data-record-trigger" disabled={!current || record?.table !== table || !visibleRows.includes(current)} onClick={() => setDrawer('record')}>查看详情 →</button></div></div><div className="data-table-toolbar"><div className="data-table-tabs">{(dataset === 'orders' ? ['products', 'orders', 'payment_events'] : ['runs', 'attempts', 'workers']).map(value => { const kind = value as TableName; return <button key={kind} className={table === kind ? 'current' : ''} onClick={() => showTable(kind)}>{tableNames[kind].join(' ')}<span aria-hidden="true">{countFor(kind) ?? '—'}</span></button> })}</div><div className="data-search-field"><input className="data-search" aria-label="搜索当前数据表" placeholder="搜索已加载记录" value={search} maxLength={100} onChange={event => { setSearch(event.target.value); setRecord(null) }} />{search && <button className="data-search-clear" aria-label="清除搜索" onClick={() => { setSearch(''); setRecord(null) }}>×</button>}</div></div>
      {dataset === 'orders' && table === 'products' && editing?.can_delete && <div className="product-visibility" role="group" aria-label="商品状态筛选">{([['active','在售'],['deleted','已删除'],['all','全部']] as const).map(([value,label]) => <button key={value} aria-pressed={productVisibility === value} className={productVisibility === value ? 'current' : ''} onClick={() => { clearRelations(); setSearch(''); setDrawer(null); setRecord(null); setProductVisibility(value) }}>{label} {value === 'all' ? orders?.counts.products : orders?.product_status_counts?.[value] ?? '—'}</button>)}</div>}
      {relationActive && <div className="data-filter">当前筛选：{focusProduct !== null && `定位商品 ${focusProduct} · `}{productId !== null && `商品 ${productId}`}{orderId && ` · 订单 ${short(orderId)}`}{runId && `任务 ${short(runId)}`}{workerId && `Worker ${short(workerId)}`}{statusFilter && `状态 ${statusNames[statusFilter] ?? statusFilter}`}<button onClick={clearRelations}>清除筛选</button></div>}
      {loading && !data ? <div className="loading-state">正在读取真实数据库快照…</div> : !data ? <div className="data-empty">暂未读取到数据，请确认数据库已准备好后刷新。</div> : statusFilter && visibleRows.length === 0 ? <div className="data-table-scroll"><div className="data-empty">已加载记录中没有匹配状态。上方计数可能包含尚未加载的记录。</div></div> : renderTable()}
      <footer className="data-table-note"><span>显示 {visibleRows.length} / 已加载 {rowsByTable[table].length} 行 · 全表 {countFor(table) ?? '—'} 条</span><span className="data-scroll-hint">宽表可横向滚动</span><span>点击行查看详情 · 每表最多 {source?.row_limit ?? '—'} 条</span></footer>
    </section>
    {drawer && <DataDrawer title={drawer === 'guide' ? '数据说明' : '记录详情'} subtitle={drawer === 'guide' ? '理解业务、平台与自动测试数据的分工' : `${record ? tableNames[record.table].join(' / ') : ''} · ${source?.database ?? ''}`} onClose={() => setDrawer(null)}>{drawer === 'guide' ? <>
      {source && <section><h3>当前数据快照</h3><Fields rows={[["数据库", <code>{source.database}</code>], ["schema", <code>{source.schema}</code>], ["读取时间", date(source.captured_at)], ...(dataset === 'orders' && orders ? [["待支付订单", orders.status_counts.pending], ["已支付订单", orders.status_counts.paid], ["已取消订单", orders.status_counts.cancelled]] as [string, ReactNode][] : [])]} /></section>}
      <section><h3>被测业务 · 商品 → 订单 → 支付事件</h3><p className="inspector-hint">订单 API 处理库存和状态，手动演示记录保存在 probe。创建时扣库存，支付不再次扣库存；pending 取消后只恢复一次。</p><div className="data-rules"><div><strong>创建订单</strong><p>库存扣减与订单写入在同一事务中完成。</p></div><div><strong>支付回调</strong><p>金额匹配才变为 paid；event_id 去重，冲突内容被拒绝。</p></div><div><strong>取消订单</strong><p>pending 变为 cancelled；已支付订单暂不支持取消。</p></div></div></section>
      <section><h3>执行平台 · 任务 → 尝试 → Worker</h3><p className="inspector-hint">Probe 将测试任务、尝试和 Worker 心跳保存在平台库。取消任务会停止 pytest，不会取消业务订单。点击任务记录可关联尝试并跳到报告。</p></section>
      <section><h3>自动测试 · 独立数据 → 报告快照</h3><p className="inspector-hint">自动测试使用 probe_test 的临时 schema；清理后到测试报告查看请求与数据库前后快照。演示数据和自动测试数据独立。</p></section>
      <section><h3>怎样读这张表</h3><p className="inspector-hint">点击或用 Enter 选择一行查看完整字段与关联。顶部计数是全表真实数量，每张表默认加载最多 50 条；搜索与关联筛选只查当前加载的行。“未加载到”不能当作“数据库不存在”。</p></section>
      <section><h3>演示操作使用哪些接口</h3><div className="data-rules"><div><strong>读取商品 · GET /products/&#123;product_id&#125;</strong><p>获取当前价格、可售库存和在售状态，供操作前后核对。</p></div><div><strong>创建订单 · POST /orders</strong><p>传商品 ID 和购买数量；成功扣库存并返回待支付订单，金额由订单 API 计算。</p></div><div><strong>查询订单 · GET /orders/&#123;order_id&#125;</strong><p>获取当前状态、数量和下单时金额；不存在返回 404。</p></div><div><strong>模拟支付 · POST /payments/callback</strong><p>传事件 ID、订单 ID 和金额。金额匹配才支付，重复事件去重。</p></div><div><strong>取消订单 · POST /orders/&#123;order_id&#125;/cancel</strong><p>待支付订单取消并恢复库存；重复取消不会重复恢复。</p></div></div><p className="inspector-hint">页面通过平台的 /api/demo 固定入口调用独立订单 API。操作窗口提供参数、状态码与原始响应说明，所有操作只用于演示业务。</p></section>
    </> : <>{recordInfo()}{current && <section className="data-record-details"><h3>完整记录</h3><button className="data-copy-id" onClick={async () => { if (record) { try { await navigator.clipboard.writeText(record.id); setCopied('已复制完整 ID') } catch { setCopied('复制失败，请手动选择完整 ID') } } }}>{copied || '复制完整 ID'}</button><details><summary>查看完整 JSON 与字段</summary><pre>{JSON.stringify(current, null, 2)}</pre></details></section>}<p className="inspector-hint data-capture-note">本次快照读取时间：{source ? date(source.captured_at) : '未读取'}。{editing?.enabled ? '仅演示商品开放管理；订单、支付事件和平台执行记录只读。' : '本页仅查看数据。'}</p></>}</DataDrawer>}
    {(editor || createOpen) && <ProductEditor product={editor} database={orders?.source.database ?? '演示库'} onClose={() => { setEditor(null); setCreateOpen(false) }} onSaved={product => finishProductChange(product, createOpen ? 'create' : 'edit')} />}
    {productAction && <ProductStateAction product={productAction.product} action={productAction.action} onClose={() => setProductAction(null)} onDone={product => finishProductChange(product, productAction.action)} />}
    {demoAction && <DemoOrderAction {...demoAction} baseURL={orders?.demo_orders?.base_url ?? 'http://127.0.0.1:8002'} onClose={() => setDemoAction(null)} onChanged={() => void refresh()} onInspectOrder={id => { setDemoAction(null); navigateTable('orders', { order: id }); inspect('orders', id) }} />}
    {editNotice && <div className="submission-toast" role="status"><span className="submission-toast-message">{editNotice}</span><button aria-label="关闭商品保存提示" onClick={() => setEditNotice('')}>×</button></div>}
  </div>
}
