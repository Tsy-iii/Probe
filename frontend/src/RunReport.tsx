import { useEffect, useRef, useState } from 'react'
import { type Detail } from './api'
import EvidencePanel from './EvidencePanel'
import { assertionComparisons, linkAssertionChecks } from './assertionFailure'

// 筛选只改变展示；全报告的计数与任务状态始终来自实际 pytest 结果。
export default function RunReport({ run }: { run: Detail }) {
  const [filter, setFilter] = useState('all')
  const [search, setSearch] = useState('')
  const [expanded, setExpanded] = useState<Record<string, boolean>>({})
  const [jumpKey, setJumpKey] = useState<string | null>(null)
  const [copiedCase, setCopiedCase] = useState<string | null>(null)
  const [manualCommand, setManualCommand] = useState<{ key: string; command: string } | null>(null)
  const caseElements = useRef(new Map<string, HTMLDetailsElement>())
  useEffect(() => {
    if (!jumpKey) return
    const element = caseElements.current.get(jumpKey)
    const scroller = element?.closest('.detail-body')
    if (element && scroller) {
      const top = scroller.scrollTop + element.getBoundingClientRect().top - scroller.getBoundingClientRect().top - 8
      scroller.scrollTo({ top: Math.max(0, top), behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' })
      element.querySelector<HTMLElement>('summary')?.focus({ preventScroll: true })
      setJumpKey(null)
    }
  }, [jumpKey, expanded, filter, search])
  if (!run.report) return null
  const cases = run.report.cases.map((test, index) => {
    const comparisons = test.outcome === 'failed' ? assertionComparisons(test.details) : []
    const assertionChecks = linkAssertionChecks(test.evidence ?? [], comparisons)
    const unlinked = comparisons.filter(comparison => !assertionChecks.some(checks => Object.values(checks).includes(comparison)))
    return { test, index, key: test.case_id ?? `${test.classname}-${test.name}-${index}`, note: run.case_selection?.cases.find(item => item.id === test.case_id), comparisons: unlinked, assertionChecks }
  })
  const term = search.trim().toLowerCase()
  const visible = cases.filter(({ test, note }) => (filter === 'all' || (filter === 'failed' ? ['failed', 'error'].includes(test.outcome) : test.outcome === filter))
    && [test.name, test.classname, test.source_file, test.case_id, note?.title].some(value => value?.toLowerCase().includes(term)))
  const firstFailure = cases.find(({ test }) => ['failed', 'error'].includes(test.outcome))
  function expandVisible(open: boolean) { setExpanded(previous => ({ ...previous, ...Object.fromEntries(visible.map(item => [item.key, open])) })) }
  function jumpToFailure() {
    if (!firstFailure) return
    setFilter('all'); setSearch(''); setExpanded(previous => ({ ...previous, [firstFailure.key]: true })); setJumpKey(firstFailure.key)
  }
  async function copyCaseCommand(key: string, id: string) {
    // 完整用例 ID 作为单个 shell 参数，避免参数中的引号或符号被执行。
    const command = `.venv/bin/python -m pytest '${id.replaceAll("'", "'\\''")}' --require-postgres -v`
    try { await navigator.clipboard.writeText(command); setCopiedCase(key); setManualCommand(null) }
    catch { setCopiedCase(null); setManualCommand({ key, command }); setExpanded(previous => ({ ...previous, [key]: true })) }
  }
  return <div className="report-content">
    <div className="case-counts"><span>总计 <b>{run.total_count}</b></span><span className="passed-text">通过 <b>{run.passed_count}</b></span><span className="failed-text">失败 <b>{run.failed_count}</b></span><span>错误 <b>{run.error_count}</b></span><span>跳过 <b>{run.skipped_count}</b></span></div>
    {['cancelled', 'timed_out', 'error'].includes(run.status) && <p className="partial-report-note">本次任务未正常完成；下方只展示实际生成的报告，不能用部分通过结果代表全部执行完成。</p>}
    <div className="report-filter"><div role="group" aria-label="报告结果筛选">{[['all', '全部'], ['failed', '失败 / 错误'], ['passed', '通过'], ['skipped', '跳过']].map(([value, label]) => <button key={value} aria-pressed={filter === value} className={filter === value ? 'current' : ''} onClick={() => setFilter(value)}>{label}</button>)}</div><input aria-label="搜索报告用例" placeholder="搜索用例名称或文件" value={search} maxLength={100} onChange={event => setSearch(event.target.value)} /></div>
    <div className="report-actions" role="group" aria-label="报告阅读工具"><button onClick={jumpToFailure} disabled={!firstFailure}>定位首个失败</button><button onClick={() => expandVisible(true)} disabled={visible.length === 0}>展开可见用例</button><button onClick={() => expandVisible(false)} disabled={visible.length === 0}>收起可见用例</button><span>{copiedCase ? '执行命令已复制 · 在 Probe 项目根目录运行' : '复制命令使用当前测试代码'}</span></div>
    <div className="report-filter-summary">显示 {visible.length} / {cases.length} 条用例 · 全部计数保持不变</div>
    <div className="test-cases">{visible.map(({ test, key, note, comparisons, assertionChecks }) => <details className={`test-case ${test.outcome}`} key={key} ref={element => { if (element) caseElements.current.set(key, element); else caseElements.current.delete(key) }} open={expanded[key] ?? ['failed', 'error'].includes(test.outcome)} onToggle={event => { const open = event.currentTarget.open; setExpanded(previous => previous[key] === open ? previous : { ...previous, [key]: open }) }}>
      <summary><span className="case-status">{test.outcome === 'passed' ? '✓' : test.outcome === 'skipped' ? '−' : '!'}</span><span className="case-name">{note?.title ?? test.name}{note && <code className="report-function-name">{test.name}</code>}<small>{test.source_file ? `${test.source_file}${test.source_line ? `:${test.source_line}` : ''}` : test.classname}</small></span><span className="case-duration">{test.duration_seconds.toFixed(3)} s</span><button className="copy-case-command" aria-label={`复制 ${test.name} 的执行命令`} disabled={!test.case_id} title={test.case_id ? '复制完整用例 ID 的 pytest 命令；在项目根目录运行当前代码' : '旧报告没有保存完整用例 ID'} onClick={event => { event.preventDefault(); event.stopPropagation(); if (test.case_id) void copyCaseCommand(key, test.case_id) }}>复制命令</button></summary>
      {manualCommand?.key === key && <div className="case-command-fallback"><p>复制失败，可手动选中命令：</p><pre>{manualCommand.command}</pre></div>}
      {['failed', 'error'].includes(test.outcome) && <section className="case-failure-summary" aria-label="用例失败信息">
        <div className="case-failure-heading"><strong>{test.outcome === 'failed' ? 'pytest 用例失败' : 'pytest 执行错误'}</strong><span>最终用例结果</span></div>
        {comparisons.length > 0 && <div className="failure-comparison-wrap"><table className="failure-comparison-table" aria-label="失败断言期望与实际对比"><thead><tr><th>失败断言</th><th>断言期望</th><th>断言实际</th><th>结果</th></tr></thead><tbody>{comparisons.map(comparison => <tr key={`${comparison.expression}:${comparison.expected}:${comparison.actual}`}><td><code>{comparison.expression}</code></td><td><code>{comparison.expected}</code></td><td><code>{comparison.actual}</code></td><td>失败</td></tr>)}</tbody></table><p>根据本次保存的 pytest 失败输出展示。</p></div>}
        {test.details ? <><pre className="failure-brief">{test.details.split('\n').find(line => line.trim())}</pre><details className="raw-assertion"><summary>原始 pytest 断言与堆栈</summary><pre className="failure-details">{test.details}</pre></details></> : <p>没有保存详细失败信息，请查看执行日志。</p>}
      </section>}
      {test.evidence?.map((evidence, number) => <EvidencePanel evidence={evidence} assertionChecks={assertionChecks[number]} key={`${evidence.label}-${number}`} />)}
      {!['failed', 'error'].includes(test.outcome) && (test.details ? <pre className="failure-details">{test.details}</pre> : <p className="case-success">{test.outcome === 'passed' ? '该用例断言通过。' : '该用例已跳过。'}</p>)}
    </details>)}</div>
    {visible.length === 0 && <div className="report-filter-empty"><p>没有符合当前筛选的用例。</p><button onClick={() => { setFilter('all'); setSearch('') }}>显示全部用例</button></div>}
  </div>
}
