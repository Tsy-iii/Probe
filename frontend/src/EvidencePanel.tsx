import type { Evidence } from './api'
import type { AssertionComparison } from './assertionFailure'

function valueText(value: unknown) {
  return typeof value === 'object' ? JSON.stringify(value) : String(value)
}

function JsonPanel({ label, value }: { label: string; value: unknown }) {
  return <div className="evidence-json"><h5>{label}</h5><pre>{JSON.stringify(value, null, 2)}</pre></div>
}

export default function EvidencePanel({ evidence, assertionChecks = {} }: { evidence: Evidence; assertionChecks?: Record<number, AssertionComparison> }) {
  const requestId = evidence.response.headers['x-request-id'] ?? evidence.request.headers['x-request-id']
  const linked = Object.keys(assertionChecks).length > 0
  return <section className="evidence-panel" aria-label={evidence.label}>
    <div className="evidence-heading"><h4>{evidence.label}</h4><span>清理前采集</span></div>
    <div className="evidence-request"><span>{evidence.request.method}</span><code>{evidence.request.path}</code><span className="evidence-http">{evidence.response.status_code === null ? `${evidence.transport_error?.type ?? '请求异常'} · 无 HTTP 响应` : `HTTP ${evidence.response.status_code}`}</span></div>
    {evidence.transport?.mode === 'network' && <div className="network-request-url"><code>{evidence.request.url}</code>{evidence.transport.timeout_seconds !== undefined && <span>请求超时设置 {evidence.transport.timeout_seconds}s</span>}</div>}
    {requestId && <div className="evidence-request-id">request_id: {requestId}</div>}
    {evidence.checks.length > 0 && <><p className="evidence-checks-scope">{linked ? '已关联的失败断言直接显示在对应行；其余行来自采集配置。' : '本次采集配置的检查项 · 最终用例状态以 pytest 执行结果为准'}</p><div className="evidence-table-wrap"><table className="evidence-table" aria-label="请求与断言检查对照"><thead><tr><th>检查项</th><th>期望</th><th>实际</th><th>对照</th><th>来源</th></tr></thead><tbody>{evidence.checks.map((check, index) => {
      const assertion = assertionChecks[index]
      const expected = assertion ? assertion.expected : check.expected
      const matches = JSON.stringify(expected) === JSON.stringify(check.actual)
      return <tr className={matches ? 'check-match' : 'check-mismatch'} key={`${check.name}-${index}`}><td>{check.name}</td><td title={assertion ? `pytest 断言期望 ${assertion.expected}；原始采集期望 ${valueText(check.expected)}` : '来自采集配置'}><code>{valueText(expected)}</code></td><td><code>{valueText(check.actual)}</code></td><td><span className="check-result">{matches ? '符合' : '不符'}</span></td><td><span className={`check-origin ${assertion ? 'assertion' : ''}`}>{assertion ? 'pytest 失败断言' : '采集配置'}</span></td></tr>
    })}</tbody></table></div></>}
    {linked && <details className="evidence-fold original-capture-checks"><summary>原始采集检查</summary><JsonPanel label="原始采集配置与结果" value={evidence.checks} /></details>}
    {evidence.transport_error && <p className="network-error-note">异常阶段：{evidence.transport_error.phase} · {evidence.transport_error.message}</p>}
    {evidence.hint && <div className="evidence-hint"><strong>测试提供的定位提示</strong><p>{evidence.hint}</p>{evidence.related_code && <code>关联实现：{evidence.related_code.file}:{evidence.related_code.line}</code>}</div>}
    <details className="evidence-fold"><summary>请求与响应</summary><div className="evidence-columns"><JsonPanel label="请求" value={evidence.request} /><JsonPanel label="响应" value={evidence.response} /></div></details>
    <details className="evidence-fold"><summary>数据库快照 · 清理后仍可查看</summary><div className="evidence-columns"><JsonPanel label="请求前" value={evidence.database.before} /><JsonPanel label={evidence.transport_error ? '客户端异常时' : '请求后'} value={evidence.database.after} /></div></details>
  </section>
}
