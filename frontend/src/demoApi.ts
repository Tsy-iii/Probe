import type { DataOrder } from './api'

export type DemoOperation = 'create' | 'pay' | 'cancel'
export type DemoOrder = Omit<DataOrder, 'product_name' | 'payment_event_count'>
export interface DemoExchange {
  request_id: string; captured_at: string; elapsed_ms?: number; entry_status: number; entry_path: string
  request: { method: string; path: string; url: string; headers: Record<string, string>; body_text: string | null }
  response: { status_code: number; body_text: string; request_id: string | null } | null
  data: Record<string, unknown> | null
  transport_error: { code: string; message: string; outcome_unknown: boolean } | null
}

export const demoInterfaces = {
  create: { title: '创建演示订单', method: 'POST', path: '/orders', entry: '/api/demo/orders',
    purpose: '购买一个商品。库存扣减和订单写入在同一事务中完成，订单进入待支付。',
    fields: [['product_id', '商品 ID，来自当前商品'], ['quantity', '购买数量，必须为正整数']],
    results: '201：创建成功；404：商品不存在；409：库存不足、商品已删除或金额溢出；422：参数错误。' },
  pay: { title: '模拟支付回调', method: 'POST', path: '/payments/callback', entry: '/api/demo/payments/callback',
    purpose: '模拟支付方的通知，不发生真实扣款。金额匹配时，待支付订单变为已支付。',
    fields: [['event_id', '稳定的事件 ID，重放时保持不变'], ['order_id', '当前订单的完整 ID'], ['amount_cents', '回调金额，订单 API 接收整数分']],
    results: '200：处理成功，duplicate 表示是否为同一事件重放；404：订单不存在；409：金额错误、订单已取消或事件内容冲突；422：参数错误。' },
  cancel: { title: '取消演示订单', method: 'POST', path: '/orders/{order_id}/cancel', entry: '/api/demo/orders/{order_id}/cancel',
    purpose: '待支付订单取消后恢复库存；同一订单重复取消不会重复恢复。已支付订单暂不支持取消。',
    fields: [['order_id', '路径中的订单 ID；没有请求体']],
    results: '200：已取消，包含重复取消；404：订单不存在；409：已支付订单不能取消；422：订单 ID 格式错误。' },
} as const

export function yuan(cents: string) { const value = BigInt(cents); return `${value / 100n}.${(value % 100n).toString().padStart(2, '0')}` }
export function parseYuan(value: string): string | null {
  if (!/^(0|[1-9]\d*)(\.\d{1,2})?$/.test(value) || value.length > 20) return null
  const [whole, fraction = ''] = value.split('.')
  const cents = BigInt(whole) * 100n + BigInt(fraction.padEnd(2, '0'))
  return cents > 0n && cents <= 9_223_372_036_854_775_807n ? cents.toString() : null
}
export function exchangeOK(record: DemoExchange) {
  return !!record.response && record.response.status_code >= 200 && record.response.status_code < 300 && !record.transport_error && !!record.data
}
const errorNames: Record<string, string> = {
  product_not_found: '商品不存在', order_not_found: '订单不存在', product_inactive: '商品已删除',
  insufficient_stock: '库存不足', payment_amount_mismatch: '支付金额与订单不一致',
  order_cancelled: '订单已取消，不能支付', order_already_paid: '订单已支付，不能取消',
  event_conflict: '事件 ID 已用于不同的订单或金额', amount_overflow: '总金额超出可支持范围',
  validation_error: '请求参数不符合接口要求', database_error: '数据库操作结果未确认',
}
export function exchangeMessage(record: DemoExchange) {
  if (record.transport_error) return record.transport_error.message
  const error = record.data?.error as { code?: string; message?: string } | undefined
  if (error) return `${errorNames[error.code ?? ''] ?? error.message ?? '业务请求被拒绝'}${error.code ? `（${error.code}）` : ''}`
  return exchangeOK(record) ? '接口已返回成功响应' : '请求未成功，请查看响应内容。'
}

export async function callDemo(method: string, path: string, body?: Record<string, unknown>, baseURL = 'http://127.0.0.1:8002'): Promise<DemoExchange> {
  const requestId = crypto.randomUUID(), entry = `/api/demo${path}`
  const fallback: DemoExchange = {
    request_id: requestId, captured_at: new Date().toISOString(), entry_path: entry, entry_status: 0,
    request: { method, path, url: baseURL + path, headers: { 'X-Request-ID': requestId }, body_text: body ? JSON.stringify(body, null, 2) : null },
    response: null, data: null, transport_error: null,
  }
  try {
    const response = await fetch(entry, { method, headers: { 'Content-Type': 'application/json', 'X-Request-ID': requestId },
      body: body ? JSON.stringify(body) : undefined, signal: AbortSignal.timeout(12000) })
    const text = await response.text()
    let document
    try { document = JSON.parse(text) } catch { document = null }
    if (document && 'request' in document && 'transport_error' in document) return { ...document, entry_status: response.status, entry_path: entry }
    return { ...fallback, entry_status: response.status, transport_error: {
      code: 'platform_request_rejected', message: `页面入口返回 HTTP ${response.status}，未获得有效的订单 API 调用记录。${response.status === 422 ? '请检查请求参数。' : '请查询核对当前状态。'}`,
      outcome_unknown: method !== 'GET' && response.status >= 500,
    }, data: document ? { platform_response: document } : { platform_response: text } }
  } catch {
    return { ...fallback, transport_error: { code: 'browser_response_unconfirmed',
      message: '未收到平台的完整响应，操作结果未确认。请先查询订单和库存，避免重复下单。', outcome_unknown: method !== 'GET' } }
  }
}
