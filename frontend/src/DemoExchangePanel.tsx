import { exchangeMessage, exchangeOK, type DemoExchange } from './demoApi'

export default function DemoExchangePanel({ records }: { records: DemoExchange[] }) {
  if (!records.length) return null
  const expanded = [...records].reverse().find(record => record.request.method === 'POST') ?? records.at(-1)
  return <section className="demo-exchanges"><h3>本次请求记录 <span>{records.length} 条</span></h3>
    <p className="inspector-hint">包含操作前后的查询。此处是手动请求记录，自动测试报告在测试工作台查看。</p>
    {[...records].reverse().map(record => <details className="demo-exchange" key={record.request_id} open={expanded === record}>
      <summary><code>{record.request.method}</code><span>{record.request.path}</span><strong className={exchangeOK(record) ? 'ok' : 'failed'}>{record.response ? `HTTP ${record.response.status_code}` : '未获得业务响应'}</strong></summary>
      <div className="demo-exchange-body">
        <p className="demo-exchange-message">{exchangeMessage(record)}</p>
        <dl><dt>被测地址</dt><dd><code>{record.request.url}</code></dd><dt>页面入口</dt><dd><code>{record.request.method} {record.entry_path}</code> · HTTP {record.entry_status || '未收到'}</dd><dt>请求 ID</dt><dd><code>{record.request_id}</code></dd><dt>响应请求 ID</dt><dd><code>{record.response?.request_id ?? '未获得'}</code></dd><dt>读取时间</dt><dd>{new Date(record.captured_at).toLocaleTimeString('zh-CN', { hour12: false })}</dd>{record.elapsed_ms !== undefined && <><dt>本次耗时</dt><dd>{record.elapsed_ms} ms（含就绪检查）</dd></>}</dl>
        <h4>{record.response ? '实际业务请求体' : '请求参数（未获得完整业务响应）'}</h4><pre>{record.request.body_text ?? '无请求体'}</pre>
        <h4>原始业务响应</h4><pre>{record.response?.body_text ?? '没有收到订单 API 的业务响应，不能据此判断事务是否提交。'}</pre>
        {!record.response && record.data?.platform_response !== undefined && <><h4>页面入口响应</h4><pre>{JSON.stringify(record.data.platform_response, null, 2)}</pre></>}
      </div>
    </details>)}
  </section>
}
