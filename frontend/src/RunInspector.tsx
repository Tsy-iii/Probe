import { useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import type { Detail, Status } from './api'

const time = (value: string | null) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'

// 完整执行信息放在侧边抽屉；打开它不会缩小报告或日志的阅读区域。
export default function RunInspector({ run, suiteName, labels, selectedAttempt, onClose, onAttempt, onSource, onHttpLog }: {
  run: Detail; suiteName: string; labels: Record<Status, string>; selectedAttempt: string | null
  onClose: () => void; onAttempt: (id: string) => void; onSource: (id: string) => void
  onHttpLog: (id: string) => void
}) {
  const panel = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'; panel.current?.focus()
    return () => { document.body.style.overflow = overflow; previous?.focus() }
  }, [])

  function keydown(event: React.KeyboardEvent<HTMLDivElement>) {
    if (event.key === 'Escape') { event.stopPropagation(); onClose() }
    if (event.key !== 'Tab' || !panel.current) return
    const items = [...panel.current.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], summary, [tabindex="0"]')]
    const first = items[0], last = items.at(-1)
    if (!first || !last) { event.preventDefault(); return }
    if (event.shiftKey && (document.activeElement === first || document.activeElement === panel.current)) { event.preventDefault(); last.focus() }
    else if (!event.shiftKey && (document.activeElement === last || document.activeElement === panel.current)) { event.preventDefault(); first.focus() }
  }

  return createPortal(<div className="run-inspector-backdrop" onClick={event => { if (event.target === event.currentTarget) onClose() }}>
    <div className="run-inspector" role="dialog" aria-modal="true" aria-labelledby="run-inspector-title" tabIndex={-1} ref={panel} onKeyDown={keydown}>
      <header className="run-inspector-heading"><div><span className="eyebrow">RUN / CONTEXT</span><h2 id="run-inspector-title">执行详情</h2><p>{suiteName} · {labels[run.status]}</p></div><button aria-label="关闭执行详情" onClick={onClose}>×</button></header>
      <div className="run-inspector-body">
        <section><h3>执行上下文</h3><dl className="inspector-fields"><dt>任务 ID</dt><dd><code>{run.id}</code></dd><dt>提交键</dt><dd><code>{run.idempotency_key}</code></dd><dt>提交时间</dt><dd>{time(run.created_at)}</dd><dt>开始时间</dt><dd>{time(run.started_at)}</dd><dt>结束时间</dt><dd>{time(run.finished_at)}</dd><dt>每次尝试超时</dt><dd>{run.timeout_seconds} s</dd><dt>退出码</dt><dd>{run.exit_code ?? '—'}</dd><dt>执行尝试</dt><dd>{run.attempt_count} / {run.max_attempts}</dd></dl>
          {run.rerun_of_id && <div className="inspector-source">重新执行自 <button onClick={() => onSource(run.rerun_of_id!)}>#{run.rerun_of_id.slice(0, 8)} · 查看原任务</button></div>}
        </section>
        {run.error_message && <section className="inspector-notice"><h3>执行说明</h3><p>{run.error_message}</p></section>}
        {run.http_service_notice && <section className="inspector-notice"><p>{run.http_service_notice}</p></section>}
        {(run.http_services ?? []).length > 0 && <section><h3>HTTP 测试服务<span>{(run.http_services ?? []).length} 个</span></h3><p className="inspector-hint">各用例独立启动服务；结束后地址可能失效，保留日志与请求证据。</p><div className="http-service-list">{(run.http_services ?? []).map(service => <details className="http-service-record" key={service.id}><summary><code>{service.base_url}</code><span>{service.status === 'ready' ? '就绪' : service.status === 'stopped' ? '已停止' : service.status === 'starting' ? '启动中' : '异常'}</span></summary><div><code className="http-service-case">{service.case_id}</code><dl className="inspector-fields"><dt>服务 PID</dt><dd>{service.process_id ?? '未记录'}</dd><dt>测试 schema</dt><dd><code>{service.isolation_schema}</code></dd><dt>服务就绪</dt><dd>{time(service.ready_at)}</dd><dt>服务停止</dt><dd>{time(service.stopped_at)}</dd><dt>清理检查</dt><dd>{service.cleanup_verified ? '监听进程已停止' : '尚未确认'}</dd></dl><button disabled={!service.log_available} onClick={() => onHttpLog(service.id)}>查看 HTTP 服务日志</button></div></details>)}</div></section>}
        {['amount_bug_demo', 'failure_demo'].includes(run.suite_id) && <section className="inspector-notice"><h3>故障演示说明</h3><p>{run.suite_id === 'amount_bug_demo' ? '金额套件包含故障与正确实现，可分别选择执行；故障用例应失败，正确用例应通过。这里的定位提示来自测试作者。' : '此演示显式注入固定 503，但断言要求 201，测试应真实失败。'}</p></section>}
        <section><h3>本次执行范围<span>{run.case_selection ? `${run.case_selection.cases.length} 条` : '旧版记录'}</span></h3>
          {run.case_selection ? <><p className="inspector-hint">{run.case_selection.mode === 'all' ? '提交时全部用例' : '选择执行'}。以下是保存的选择，实际结果请看测试报告。</p><div className="inspector-case-list">{run.case_selection.cases.map(test => <div className="run-selected-case" key={test.id}><strong>{test.title}</strong><code>{test.id}</code></div>)}</div></> : <p className="inspector-hint">旧任务未保存具体用例选择，可通过原报告查看实际执行结果。</p>}
        </section>
        <section><h3>执行尝试<span>{run.attempts.length} 条</span></h3><p className="inspector-hint">点击一次尝试，回到对应的执行日志。</p>
          {run.attempts.length ? <div className="inspector-attempt-list">{run.attempts.map(attempt => <button key={attempt.id} className={`attempt-row ${selectedAttempt === attempt.id ? 'chosen-attempt' : ''}`} onClick={() => onAttempt(attempt.id)}><span className={`attempt-dot ${attempt.status}`} /><span className="attempt-copy"><strong>第 {attempt.number} 次 · {attempt.status === 'interrupted' ? 'Worker 中断' : labels[attempt.status as Status] ?? attempt.status}</strong><small>Worker {attempt.worker_id.slice(0, 8)}</small><small>{attempt.isolation_schema ?? '独立工作目录'}</small><small>{attempt.cleanup_done ? '已清理进程 / 数据' : '清理尚未完成'}{attempt.stop_reason ? ` · ${attempt.stop_reason}` : ''}</small></span><span className="inspector-log-link">查看日志 →</span></button>)}</div> : <p className="inspector-hint">当前没有执行尝试记录；排队取消的任务未启动 pytest。</p>}
        </section>
      </div>
    </div>
  </div>, document.body)
}
