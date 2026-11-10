import { useRef, useState, type FormEvent } from 'react'
import { api, ApiError, type DataProduct, type ProductDetail, type ProductSaved } from './api'
import DataDrawer from './DataDrawer'

const maxPrice = 9_223_372_036_854_775_807n
function yuan(cents: string) { const value = BigInt(cents); return `${value / 100n}.${(value % 100n).toString().padStart(2, '0')}` }
function draftValues(price: string, stock: string) {
  let priceError = '', stockError = '', cents = 0n
  if (!price) priceError = '请输入商品单价。'
  else if (!/^(0|[1-9]\d*)(\.\d{1,2})?$/.test(price) || price.length > 20) priceError = '请输入大于 0、最多两位小数的金额。'
  else {
    const [whole, fraction = ''] = price.split('.')
    cents = BigInt(whole) * 100n + BigInt(fraction.padEnd(2, '0'))
    if (cents <= 0n) priceError = '单价须大于 0。'
    else if (cents > maxPrice) priceError = '金额超出可支持的范围。'
  }
  if (!/^(0|[1-9]\d*)$/.test(stock) || Number(stock) > 2_147_483_647) stockError = '库存须为 0～2147483647 的整数。'
  return { priceError, stockError, price_cents: cents.toString(), stock: Number(stock) }
}

export default function ProductEditor({ product, database, onClose, onSaved }: { product: DataProduct | null; database: string; onClose: () => void; onSaved: (product: DataProduct) => void }) {
  const creating = product === null
  const [baseline, setBaseline] = useState(product ?? { id: 0, name: '', price_cents: '0', stock: 0 })
  const [name, setName] = useState('')
  const [price, setPrice] = useState(() => product ? yuan(product.price_cents) : '')
  const [stock, setStock] = useState(String(product?.stock ?? 0))
  const [saving, setSaving] = useState(false), [reloading, setReloading] = useState(false)
  const [error, setError] = useState(''), [editable, setEditable] = useState(true)
  const [touched, setTouched] = useState({ name: false, price: false, stock: false })
  const [submitted, setSubmitted] = useState(false)
  const [blocked, setBlocked] = useState(false)
  const [discarding, setDiscarding] = useState(false)
  const form = useRef<HTMLFormElement>(null)
  const values = draftValues(price, stock)
  const nameError = creating ? !name.trim() ? '请输入商品名称。' : name.trim().length > 100 ? '名称最多 100 个字符。' : '' : ''
  const invalid = !!values.priceError || !!values.stockError || nameError
  const changed = values.price_cents !== baseline.price_cents || values.stock !== baseline.stock
  const busy = saving || reloading
  const nameFeedback = (touched.name || submitted) && nameError
  const priceFeedback = (touched.price || submitted) && values.priceError
  const stockFeedback = (touched.stock || submitted) && values.stockError
  const dirty = creating ? !!name || !!price || stock !== '0' : price !== yuan(baseline.price_cents) || stock !== String(baseline.stock)
  function close() { if (!busy) { if (dirty) setDiscarding(true); else onClose() } }

  async function save(event: FormEvent) {
    event.preventDefault()
    if (busy || !editable || blocked) return
    setSubmitted(true); setDiscarding(false)
    if (invalid) {
      const field = nameError ? 'create-product-name' : values.priceError ? 'edit-product-price' : 'edit-product-stock'
      form.current?.querySelector<HTMLInputElement>(`#${field}`)?.focus()
      return
    }
    if (!creating && !changed) return
    setSaving(true); setError('')
    try {
      const body = creating ? { name: name.trim(), price_cents: values.price_cents, stock: values.stock } : { price_cents: values.price_cents, stock: values.stock, expected_price_cents: baseline.price_cents, expected_stock: baseline.stock }
      const result = await api<ProductSaved>(creating ? '/data/products' : `/data/products/${baseline.id}`, { method: creating ? 'POST' : 'PATCH', signal: AbortSignal.timeout(10000), headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      onSaved(result.product)
    } catch (exception) {
      if (!creating && exception instanceof ApiError && exception.status === 409) { setBlocked(true); setError('商品状态、价格或库存已变化，未覆盖其他操作。请重新读取后确认。') }
      else if (exception instanceof ApiError && exception.status >= 400 && exception.status < 500) setError(`请求被拒绝：${exception.message}`)
      else { setBlocked(true); setError(creating ? '新增结果未确认。请关闭表单并刷新商品列表确认结果，避免重复新增。' : `保存结果未确认：${exception instanceof Error ? exception.message : '请求异常'}。请重新读取商品确认当前值。`) }
    } finally { setSaving(false) }
  }
  async function reload() {
    setReloading(true); setError('')
    try {
      const result = await api<ProductDetail>(`/data/products/${baseline.id}`, { signal: AbortSignal.timeout(8000) })
      setBaseline(result.product); setPrice(yuan(result.product.price_cents)); setStock(String(result.product.stock)); setEditable(result.product_editing.enabled && result.product.is_active !== false)
      setBlocked(false); setTouched({ name: false, price: false, stock: false }); setSubmitted(false); setDiscarding(false)
      if (!result.product_editing.enabled) setError(result.product_editing.reason)
      else if (result.product.is_active === false) setError('商品已删除，请先恢复后再编辑。')
    } catch (exception) { setError(exception instanceof Error ? exception.message : '重新读取失败') }
    finally { setReloading(false) }
  }

  return <DataDrawer title={creating ? "新增演示商品" : "编辑演示商品"} subtitle={creating ? `${database} · 创建可售商品` : `${database} · 商品 ${baseline.id} · ${baseline.name}`} eyebrow="DATA / PRODUCT" closeLabel={creating ? "关闭商品新增" : "关闭商品编辑"} busy={busy} onClose={close}>
    <form className="product-editor" ref={form} noValidate onSubmit={save}>
      <p className="product-edit-scope">{creating ? '创建后可通过订单 API 下单并观察库存变化。' : '修改后，后续订单使用新的价格和可售库存。'}自动测试使用独立数据。</p>
      {creating && <div className="product-name-field"><label htmlFor="create-product-name">商品名称<input id="create-product-name" aria-label="商品名称" data-initial-focus placeholder="例如：流程体验商品" value={name} disabled={busy || !editable} onChange={event => setName(event.target.value)} onBlur={() => setTouched(value => ({ ...value, name: true }))} aria-describedby="product-name-feedback" aria-invalid={!!nameFeedback} /></label><p id="product-name-feedback" className={nameFeedback ? 'product-field-error' : 'product-field-hint'}>{nameFeedback || '填写 1～100 个字符，便于在数据表中识别。'}</p></div>}
      <label htmlFor="edit-product-price">单价（元）<input id="edit-product-price" aria-label="商品单价（元）" type="text" inputMode="decimal" placeholder="29.90" data-initial-focus={creating ? undefined : true} value={price} disabled={busy || !editable} onChange={event => setPrice(event.target.value)} onBlur={() => setTouched(value => ({ ...value, price: true }))} aria-describedby="product-price-feedback" aria-invalid={!!priceFeedback} /></label>
      <p id="product-price-feedback" className={priceFeedback ? 'product-field-error' : 'product-field-hint'}>{priceFeedback || (!values.priceError ? `保存为整数 ${values.price_cents} 分${creating ? '。' : '，已有订单金额保持不变。'}` : '例如 29.90，金额须大于 0，最多两位小数。')}</p>
      <label htmlFor="edit-product-stock">可售库存（件）<input id="edit-product-stock" aria-label="商品可售库存" type="text" inputMode="numeric" value={stock} disabled={busy || !editable} onChange={event => setStock(event.target.value)} onBlur={() => setTouched(value => ({ ...value, stock: true }))} aria-describedby="product-stock-feedback" aria-invalid={!!stockFeedback} /></label>
      <p id="product-stock-feedback" className={stockFeedback ? 'product-field-error' : 'product-field-hint'}>{stockFeedback || '0 表示暂时无可售库存；待支付订单取消时会恢复库存。'}</p>
      {!creating && <div className="product-edit-baseline"><strong>读取时的原值</strong><span>单价 {yuan(baseline.price_cents)} 元 · 库存 {baseline.stock} 件</span><p>保存时比较原值，价格或库存变化会要求重新读取。</p></div>}
      {error && <div className="product-save-error" role="alert">{error}{!creating && <button type="button" disabled={busy} onClick={() => void reload()}>{reloading ? '读取中…' : '重新读取商品'}</button>}{!creating && <small>重新读取会放弃当前草稿，并展示最新值。</small>}</div>}
      {discarding && <div className="product-discard-notice" role="status"><span>{blocked ? '操作结果尚未确认，返回列表后请刷新核对。' : '当前内容尚未保存，是否放弃？'}</span><div><button type="button" onClick={() => setDiscarding(false)}>继续编辑</button><button type="button" onClick={onClose}>{blocked ? '返回列表' : '放弃修改'}</button></div></div>}
      <div className="product-editor-actions"><button type="button" disabled={busy} onClick={close}>取消</button><button className="product-save-button" type="submit" disabled={busy || (!creating && !changed) || !editable || blocked}>{saving ? '保存中…' : creating ? '新增商品' : '保存修改'}</button></div>
    </form>
  </DataDrawer>
}
