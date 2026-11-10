import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import type { DataOrder, DataProduct } from './api'
import DataDrawer from './DataDrawer'
import DemoExchangePanel from './DemoExchangePanel'
import { callDemo, demoInterfaces, exchangeMessage, exchangeOK, parseYuan, yuan, type DemoExchange, type DemoOperation, type DemoOrder } from './demoApi'

interface Props {
  operation: DemoOperation; product?: DataProduct; order?: DataOrder; baseURL: string
  onClose: () => void; onChanged: () => void; onInspectOrder: (id: string) => void
}

export default function DemoOrderAction({ operation, product, order, baseURL, onClose, onChanged, onInspectOrder }: Props) {
  const definition = demoInterfaces[operation]
  const [latestProduct, setLatestProduct] = useState<DataProduct | null>(null)
  const [latestOrder, setLatestOrder] = useState<DemoOrder | null>(null)
  const [quantity, setQuantity] = useState('1')
  const [eventId, setEventId] = useState(() => `demo-pay-${crypto.randomUUID()}`)
  const [amount, setAmount] = useState(() => order ? yuan(order.total_amount_cents) : '')
  const [touched, setTouched] = useState(false)
  const [busy, setBusy] = useState(true), [ready, setReady] = useState(false)
  const [records, setRecords] = useState<DemoExchange[]>([])
  const [result, setResult] = useState<DemoExchange | null>(null)
  const [unknown, setUnknown] = useState(false)
  const [readError, setReadError] = useState('')
  const running = useRef(false), active = useRef(false)
  const generation = useRef(0), focused = useRef(false)
  const form = useRef<HTMLFormElement>(null)
  const lastPayload = useRef<Record<string, unknown> | undefined>(undefined)
  const productId = product?.id ?? order?.product_id
  const orderId = order?.id
  const collect = useCallback((record: DemoExchange) => {
    if (active.current) setRecords(items => [...items.slice(-11), record])
  }, [])

  const readCurrent = useCallback(async (afterOperation = false) => {
    if (productId === undefined) return false
    const version = generation.current
    const queries = [callDemo('GET', `/products/${productId}`, undefined, baseURL)]
    if (orderId) queries.push(callDemo('GET', `/orders/${orderId}`, undefined, baseURL))
    const replies = await Promise.all(queries)
    if (!active.current || generation.current !== version) return false
    replies.forEach(collect)
    const successful = replies.every(exchangeOK)
    setReadError(successful ? '' : `当前数据读取未完成：${replies.filter(item => !exchangeOK(item)).map(exchangeMessage).join('；')}`)
    if (exchangeOK(replies[0])) setLatestProduct(replies[0].data as unknown as DataProduct)
    if (replies[1] && exchangeOK(replies[1])) setLatestOrder(replies[1].data as unknown as DemoOrder)
    if (!afterOperation) setReady(successful)
    return successful
  }, [productId, orderId, baseURL, collect])

  useEffect(() => {
    active.current = true
    generation.current += 1
    let cancelled = false
    void readCurrent().finally(() => { if (!cancelled) setBusy(false) })
    return () => { cancelled = true; active.current = false; generation.current += 1 }
  }, [readCurrent])
  useEffect(() => {
    if (!busy && ready && !focused.current) { form.current?.querySelector<HTMLInputElement>('[data-initial-focus]')?.focus({ preventScroll: true }); focused.current = true }
  }, [busy, ready])

  const quantityValid = /^(0|[1-9]\d*)$/.test(quantity) && Number(quantity) > 0 && Number(quantity) <= 2_147_483_647
  const amountCents = parseYuan(amount)
  const eventValid = /^[A-Za-z0-9_.:-]{1,100}$/.test(eventId)
  const preview = latestProduct && quantityValid ? BigInt(latestProduct.price_cents) * BigInt(quantity) : null
  const overflow = preview !== null && preview > 9_223_372_036_854_775_807n
  const invalid = operation === 'create' ? !quantityValid || overflow : operation === 'pay' ? !eventValid || !amountCents : false
  const created = operation === 'create' && !!result && exchangeOK(result)
  const status = latestOrder?.status
  const stateConflict = operation === 'create' ? latestProduct?.is_active === false
    : operation === 'pay' ? status === 'cancelled' : status === 'paid'
  const blocked = busy || !ready || unknown || created || stateConflict

  async function refreshCurrent() {
    if (running.current) return
    running.current = true; setBusy(true)
    try {
      const confirmed = await readCurrent()
      // 创建结果不明时，仅库存读回无法识别是否产生了新订单；该表单不允许重发。
      if (confirmed && operation !== 'create') setUnknown(false)
    } finally { running.current = false; if (active.current) setBusy(false) }
  }

  async function send(payload?: Record<string, unknown>) {
    if (running.current || blocked) return
    running.current = true; setBusy(true); setTouched(true)
    const path = operation === 'create' ? '/orders' : operation === 'pay' ? '/payments/callback' : `/orders/${orderId}/cancel`
    try {
      const reply = await callDemo('POST', path, payload, baseURL)
      if (!active.current) return
      collect(reply); setResult(reply); lastPayload.current = payload
      setUnknown(reply.transport_error?.outcome_unknown ?? false)
      if (exchangeOK(reply)) {
        const returned = (operation === 'pay' ? reply.data?.order : reply.data) as DemoOrder
        setLatestOrder(returned)
        onChanged()
        await readCurrent(true)
      }
    } finally { running.current = false; if (active.current) setBusy(false) }
  }

  function submit(event: FormEvent) {
    event.preventDefault(); setTouched(true)
    if (invalid || blocked) return
    const payload = operation === 'create' ? { product_id: productId, quantity: Number(quantity) }
      : operation === 'pay' ? { event_id: eventId, order_id: orderId, amount_cents: amountCents } : undefined
    void send(payload)
  }
  const resultOrder = result && exchangeOK(result) ? (operation === 'pay' ? result.data?.order : result.data) as DemoOrder : null
  const duplicate = operation === 'pay' && result?.data?.duplicate === true
  const successText = operation === 'create' ? '订单已创建，当前为待支付。' : operation === 'pay' ? duplicate ? '同一支付事件已识别为重复通知，未重复改变订单。' : '模拟支付回调已处理，订单为已支付。' : '订单已取消；重复取消不会重复恢复库存。'

  return <DataDrawer title={definition.title} subtitle={`${product?.name ?? order?.product_name ?? '演示业务'} · ${operation === 'create' ? `商品 ${productId}` : `订单 ${orderId}`}`} eyebrow="DATA / ORDER API" closeLabel="关闭订单操作" busy={busy} onClose={onClose}>
    <div className="demo-api-intro"><code><b>{definition.method}</b> {definition.path}</code><p>{definition.purpose}</p></div>
    <details className="demo-api-guide"><summary>接口说明 · 参数、响应与状态规则</summary><div><dl>{definition.fields.map(([field, explanation]) => <div key={field}><dt><code>{field}</code></dt><dd>{explanation}</dd></div>)}</dl><p>{definition.results}</p><p>页面入口：<code>{definition.entry}</code>。服务未就绪返回 503；结果未确认时先查询核对。</p><p>{operation === 'create' ? '金额以提交时服务实际返回为准；修改商品价格不改变已有订单金额。' : operation === 'pay' ? '重放使用相同事件 ID、订单 ID 和金额；同一事件 ID 携带不同内容会被拒绝。页面金额以元填写，提交给平台时为字符串分，再转为订单 API 的整数分。' : '取消业务订单和取消测试任务是两个独立操作。'}</p><a href={`${baseURL}/docs`} target="_blank" rel="noreferrer">打开订单 API 文档 ↗</a></div></details>
    <div className="demo-current-state"><span>读取到的当前数据</span><strong>{latestProduct ? `可售库存 ${latestProduct.stock} 件 · 当前单价 ${yuan(latestProduct.price_cents)} 元` : '正在读取商品…'}</strong>{latestOrder && <p>订单状态：{({ pending: '待支付', paid: '已支付', cancelled: '已取消' })[latestOrder.status]} · 订单金额 {yuan(latestOrder.total_amount_cents)} 元</p>}<button type="button" disabled={busy} onClick={() => void refreshCurrent()}>{busy ? '读取 / 处理中…' : '重新读取当前数据'}</button></div>
    {readError && <div className="product-save-error" role="alert">{readError}</div>}
    {stateConflict && <p className="demo-state-notice">{operation === 'create' ? '商品已删除，请先恢复后再下单。' : operation === 'pay' ? '订单已取消，不能再支付。' : '订单已支付，暂不支持取消。'}</p>}
    <form className="product-editor demo-order-form" ref={form} noValidate onSubmit={submit}>
      {operation === 'create' && <><label htmlFor="demo-order-quantity">购买数量<input id="demo-order-quantity" data-initial-focus aria-label="购买数量" aria-describedby="demo-quantity-feedback" inputMode="numeric" value={quantity} disabled={busy || created} onChange={event => setQuantity(event.target.value)} onBlur={() => setTouched(true)} aria-invalid={touched && (!quantityValid || overflow)} /></label><p id="demo-quantity-feedback" className={touched && (!quantityValid || overflow) ? 'product-field-error' : 'product-field-hint'}>{!quantityValid && touched ? '请输入 1～2147483647 的整数。' : overflow ? '预计金额超出可支持范围。' : preview !== null ? `预计总金额 ${yuan(preview.toString())} 元（${preview} 分）。库存不足会由接口明确拒绝。` : '填写正整数数量，金额由订单服务计算。'}</p></>}
      {operation === 'pay' && <><label htmlFor="demo-payment-event">支付事件 ID<input id="demo-payment-event" data-initial-focus aria-label="支付事件 ID" aria-describedby="demo-event-feedback" value={eventId} disabled={busy} onChange={event => setEventId(event.target.value)} onBlur={() => setTouched(true)} aria-invalid={touched && !eventValid} /></label><p id="demo-event-feedback" className={touched && !eventValid ? 'product-field-error' : 'product-field-hint'}>{touched && !eventValid ? '使用 1～100 个字母、数字、下划线、短横线、点或冒号。' : '本次打开只生成一次；重复发送时保持相同 ID。'}</p><label htmlFor="demo-payment-amount">回调金额（元）<input id="demo-payment-amount" aria-label="回调金额（元）" aria-describedby="demo-amount-feedback" inputMode="decimal" value={amount} disabled={busy} onChange={event => setAmount(event.target.value)} onBlur={() => setTouched(true)} aria-invalid={touched && !amountCents} /></label><p id="demo-amount-feedback" className={touched && !amountCents ? 'product-field-error' : 'product-field-hint'}>{touched && !amountCents ? '请输入大于 0、最多两位小数的金额。' : '默认使用订单金额。可以修改金额，观察金额不一致时的 409 响应。'}</p></>}
      {operation === 'cancel' && <p className="demo-cancel-confirm">确认取消这笔 {order?.quantity} 件商品、{order ? yuan(order.total_amount_cents) : '—'} 元的订单？接口会按业务规则恢复库存。</p>}
      <div className="product-editor-actions"><button type="button" disabled={busy} onClick={onClose}>返回</button><button className="product-save-button" type="submit" disabled={blocked}>{busy ? '处理中…' : created ? '订单已创建' : operation === 'create' ? '创建订单' : operation === 'pay' ? '发送支付回调' : '确认取消订单'}</button></div>
    </form>
    {result && <section className={`demo-operation-result ${exchangeOK(result) ? 'success' : 'failure'}`} role={exchangeOK(result) ? 'status' : 'alert'}><h3>{exchangeOK(result) ? '业务操作结果' : '本次操作未成功确认'}</h3><p>{exchangeOK(result) ? successText : exchangeMessage(result)}</p>{resultOrder && <><code className="demo-result-order-id">{resultOrder.id}</code><p>实际数量 {resultOrder.quantity} · 订单金额 {yuan(resultOrder.total_amount_cents)} 元{latestProduct ? ` · 读取到的库存 ${latestProduct.stock} 件` : ''}</p><div className="demo-result-actions"><button disabled={busy} onClick={() => onInspectOrder(resultOrder.id)}>查看订单详情 →</button>{operation !== 'create' && <button disabled={busy || unknown || stateConflict} onClick={() => void send(lastPayload.current)}>{operation === 'pay' ? '重放同一支付回调' : '重复取消（验证幂等）'}</button>}</div></>}{unknown && <p>{operation === 'create' ? '下单结果无法仅凭库存确认。请先到订单表核对，本表单暂不允许再次提交。' : '请先重新读取该订单和库存，确认当前状态后再明确操作。'}</p>}</section>}
    <DemoExchangePanel records={records} />
  </DataDrawer>
}
