import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type CheckStatus, type EnvironmentReport } from './api'
import DataDrawer from './DataDrawer'

const labels: Record<CheckStatus, string> = { ok: '正常', waiting: '等待', unavailable: '不可用', unknown: '未知' }
const overall = { ready: '基础运行环境就绪', partial: '部分套件具备运行条件', waiting: '等待 Worker', blocked: '环境尚未就绪' }
const definitions = [['platform_database', '平台数据库'], ['test_database', '独立测试数据库'], ['platform_test_database', '独立平台测试数据库'], ['workers', 'Worker 心跳'], ['artifacts', '报告目录'], ['order_api', '手动订单 API']]

export default function EnvironmentPanel({ suiteId, onClose }: { suiteId: string; onClose: () => void }) {
  const [report, setReport] = useState<EnvironmentReport | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const request = useRef<AbortController | null>(null)
  const alive = useRef(true)
  const refresh = useCallback(async () => {
    request.current?.abort()
    const controller = new AbortController(); request.current = controller
    const timeout = window.setTimeout(() => controller.abort('timeout'), 12000)
    setLoading(true); setError(''); setReport(null)
    try {
      const data = await api<EnvironmentReport>('/environment', { signal: controller.signal })
      if (alive.current && !controller.signal.aborted) setReport(data)
    } catch (exception) {
      if (alive.current && request.current === controller) setError(controller.signal.reason === 'timeout' ? '环境检查超过等待时间，请检查平台 API 后重试。' : exception instanceof Error ? exception.message : '无法读取环境检查结果')
    } finally {
      window.clearTimeout(timeout)
      if (alive.current && request.current === controller) setLoading(false)
    }
  }, [])
  useEffect(() => { alive.current = true; void refresh(); return () => { alive.current = false; request.current?.abort() } }, [refresh])
  const suite = report?.suites.find(item => item.id === suiteId)
  const apiStatus: CheckStatus = loading ? 'unknown' : error ? 'unavailable' : report ? 'ok' : 'unknown'
  return <DataDrawer title="运行环境检查" subtitle="逐项查看实际条件，检查结果不代表测试通过。" eyebrow="SYSTEM / ENVIRONMENT" closeLabel="关闭环境检查" onClose={onClose}>
    <div className="environment-toolbar"><span>{loading ? '检查中…' : report ? `检查于 ${new Date(report.checked_at).toLocaleString('zh-CN', { hour12: false })}` : '尚未获得检查结果'}</span><button onClick={() => void refresh()} disabled={loading}>{loading ? '正在检查' : '重新检查'}</button></div>
    {error && <div className="alert" role="alert">{error}。请确认平台 API 已启动并更新；无法获得结果时，其他依赖状态显示未知。</div>}
    {report && <section className={`environment-summary ${report.status}`}><strong>{overall[report.status]}</strong><p>{report.can_submit ? '平台数据库通过检查，任务可保存；执行还需下方条件。Worker 离线时任务可以入队等待。' : '平台依赖尚未就绪，先修复数据库再提交任务。'}</p>{suite && <><span>当前选择：{suite.name}</span><p>{suite.requires_postgres ? suite.requires_platform_test ? '此套件使用 probe_test 和 probe_platform_test，两份测试数据都按执行尝试隔离。' : '此套件需要独立 PostgreSQL 测试库 probe_test。' : '此套件不依赖订单测试数据库，仍需平台数据库、Worker 和报告目录。'}</p><p>{suite.execution_ready ? '此套件所需的基础条件通过检查。' : `等待处理：${suite.blocked_by.map(id => definitions.find(([key]) => key === id)?.[1] ?? id).join('、')}。`}</p></>}</section>}
    <div className="environment-checks" aria-busy={loading}>
      <section className="environment-check" data-check-id="platform_api"><header><h3>平台 API 检查接口</h3><span className={`environment-state ${apiStatus}`}>{labels[apiStatus]}</span></header><p>{report ? '已收到环境检查接口的响应；业务操作还依赖下方数据库状态。' : error ? '当前未取得可用的环境检查结果。' : '正在等待检查接口响应。'}</p>{report && <details className="environment-proof"><summary>请求关联</summary><code>request_id: {report.request_id}</code></details>}</section>
      {definitions.map(([id, name]) => {
        const check = report?.checks.find(item => item.id === id)
        return <section className="environment-check" data-check-id={id} key={id}><header><h3>{name}{id === 'order_api' && <small>手动演示</small>}</h3><span className={`environment-state ${check?.status ?? 'unknown'}`}>{labels[check?.status ?? 'unknown']}</span></header><p>{check?.message ?? '未读取到此项检查结果。'}</p>{check?.action && <div className="environment-action"><span>处理步骤</span><p>{check.action}</p></div>}{check && <details className="environment-proof"><summary>检查证据 · {check.duration_ms} ms</summary><pre>{JSON.stringify({ checked_at: check.checked_at, code: check.code, ...check.details }, null, 2)}</pre></details>}</section>
      })}
    </div>
    <p className="environment-scope">检查针对平台 API 的当前配置；Worker 在线依据近期心跳。8002 服务用于手动演示，当前 TestClient 自动测试不依赖它。结果是本次快照，修复环境后点击重新检查。</p>
  </DataDrawer>
}
