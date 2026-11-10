import { useState } from 'react'
import { api, ApiError, type DataProduct, type ProductSaved } from './api'
import DataDrawer from './DataDrawer'

export default function ProductStateAction({ product, action, onClose, onDone }: { product: DataProduct; action: 'delete' | 'restore'; onClose: () => void; onDone: (product: DataProduct) => void }) {
  const [busy, setBusy] = useState(false), [error, setError] = useState('')
  async function perform() {
    setBusy(true); setError('')
    try {
      const result = await api<ProductSaved>(`/data/products/${product.id}${action === 'restore' ? '/restore' : ''}`, { method: action === 'restore' ? 'POST' : 'DELETE', signal: AbortSignal.timeout(10000) })
      onDone(result.product)
    } catch (exception) { setError(exception instanceof ApiError && exception.status < 500 ? exception.message : '操作结果未确认，请关闭后刷新商品列表确认状态。') }
    finally { setBusy(false) }
  }
  return <DataDrawer title={action === 'delete' ? '删除演示商品' : '恢复演示商品'} subtitle={`商品 ${product.id} · ${product.name}`} closeLabel="关闭商品状态操作" busy={busy} onClose={onClose}>
    <p className="product-edit-scope">{action === 'delete' ? '采用软删除：停止新订单，保留商品、库存、价格和历史订单。已有订单仍可查询、支付或取消。' : '重新启用同一个商品，保留当前价格和库存。删除期间订单取消所恢复的库存也会保留。'}</p>
    <div className="product-edit-baseline"><strong>{product.name}</strong><span>商品 ID {product.id} · 价格 {product.price_cents} 分 · 库存 {product.stock} 件</span></div>
    {error && <div className="product-save-error" role="alert">{error}</div>}
    <div className="product-editor-actions"><button disabled={busy} onClick={onClose}>取消</button><button className={action === 'delete' ? 'product-delete-button' : 'product-save-button'} disabled={busy} onClick={() => void perform()}>{busy ? '处理中…' : action === 'delete' ? '确认删除（可恢复）' : '确认恢复'}</button></div>
  </DataDrawer>
}
