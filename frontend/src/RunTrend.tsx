import { useEffect, useId, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { api, type Run, type RunList, type Status, type Suite } from './api'
import WorkbenchSelect from './WorkbenchSelect'

const labels: Record<Status, string> = { queued: '待执行', running: '执行中', cancelling: '取消中', recovering: '恢复中', passed: '已通过', failed: '测试失败', error: '执行异常', timed_out: '执行超时', cancelled: '已取消' }
const outcomes = [
  { key: 'passed_count', label: '通过', className: 'passed' },
  { key: 'failed_count', label: '失败', className: 'failed' },
  { key: 'error_count', label: '错误', className: 'error' },
  { key: 'skipped_count', label: '跳过', className: 'skipped' },
] as const
const unfinished = (run: Run) => !['passed', 'failed'].includes(run.status)
const timestamp = (value: string) => new Date(value).toLocaleString('zh-CN', { hour12: false })
const shortTime = (value: string) => new Date(value).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false })

// 只有总计和四类计数完整且一致，才能绘制堆叠柱；null 不能补成 0。
function hasCounts(run: Run): boolean {
  const values = [run.total_count, ...outcomes.map(item => run[item.key])]
  return values.every(value => value !== null && Number.isInteger(value) && value >= 0)
    && outcomes.reduce((sum, item) => sum + run[item.key]!, 0) === run.total_count
}

// 按实际保存的用例 ID 分组；全选和手选同一组 ID 可以比较。旧记录独立展示。
function scopeKey(run: Run): string {
  return run.case_selection ? JSON.stringify(run.case_selection.cases.map(test => test.id).sort()) : 'unrecorded'
}

interface Props {
  suites: Suite[]; suiteId: string; refreshToken: string | null
  expanded: boolean; enabled: boolean; selectedId: string | null
  onToggle: () => void; onOpenRun: (id: string) => void; onSuiteChange: (id: string) => void
}

export default function RunTrend({ suites, suiteId, refreshToken, expanded, enabled, selectedId, onToggle, onOpenRun, onSuiteChange }: Props) {
  const bodyId = useId(), tooltipId = useId()
  const tooltipAnchor = useRef<HTMLElement | null>(null)
  const [rangeKey, setRangeKey] = useState<string | null>(null)
  const [data, setData] = useState<{ suiteId: string; list: RunList; readAt: string } | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const [retry, setRetry] = useState(0)
  const [tooltip, setTooltip] = useState<{ id: string; left: number; top: number; width: number } | null>(null)

  // 套件选择由工作台统一管理；切换后重置比较范围，避免沿用另一套件的选择。
  useEffect(() => { setRangeKey(null); setTooltip(null); setError('') }, [suiteId])
  useEffect(() => {
    if (!expanded || !enabled) return
    const controller = new AbortController()
    setLoading(true)
    void api<RunList>(`/runs?${new URLSearchParams({ suite_id: suiteId, limit: '100', offset: '0' })}`, { signal: controller.signal })
      .then(list => { if (!controller.signal.aborted) { setData({ suiteId, list, readAt: new Date().toISOString() }); setError('') } })
      .catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : '无法读取执行记录') })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [suiteId, expanded, enabled, refreshToken, retry])

  useEffect(() => {
    if (!tooltip) return
    const close = () => setTooltip(null)
    // 键盘聚焦会触发浏览器自动滚动，此时更新提示位置，避免提示立刻消失。
    const scroll = () => {
      const anchor = tooltipAnchor.current
      if (anchor === document.activeElement && anchor?.matches(':focus-visible')) showTooltip({ id: tooltip.id }, anchor)
      else close()
    }
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') close() }
    window.addEventListener('resize', close)
    document.addEventListener('scroll', scroll, true)
    document.addEventListener('keydown', escape)
    return () => { window.removeEventListener('resize', close); document.removeEventListener('scroll', scroll, true); document.removeEventListener('keydown', escape) }
  }, [tooltip])

  const current = data?.suiteId === suiteId ? data : null
  const groups = useMemo(() => {
    const collected = new Map<string, { key: string; latest: Run; runs: Run[] }>()
    for (const run of current?.list.items ?? []) {
      const key = scopeKey(run)
      const group = collected.get(key)
      if (group) group.runs.push(run)
      else collected.set(key, { key, latest: run, runs: [run] })
    }
    return [...collected.values()]
  }, [current])
  const group = groups.find(item => item.key === rangeKey) ?? groups.find(item => item.key !== 'unrecorded') ?? groups[0]
  // API 按创建时间倒序。先选最新 20 次，再反转，确保从左到右是旧 → 新。
  const runs = group?.runs.slice(0, 20).reverse() ?? []
  const hasReportedCounts = runs.some(hasCounts)
  const maxCount = Math.max(1, ...runs.filter(hasCounts).map(run => run.total_count!))
  const focusedRun = tooltip && expanded && enabled && !error ? runs.find(run => run.id === tooltip.id) : null
  const suiteName = suites.find(suite => suite.id === suiteId)?.name ?? suiteId

  function showTooltip(run: Pick<Run, 'id'>, anchor: HTMLElement) {
    tooltipAnchor.current = anchor
    const rect = anchor.getBoundingClientRect()
    const width = Math.min(310, window.innerWidth - 24)
    const left = Math.max(12, Math.min(rect.left + rect.width / 2 - width / 2, window.innerWidth - width - 12))
    const top = Math.max(12, Math.min(rect.bottom + 8, window.innerHeight - 370))
    setTooltip({ id: run.id, left, top, width })
  }

  return <section className={`run-trend ${expanded ? 'is-expanded' : ''}`} aria-label="测试执行趋势">
    <div className="trend-heading">
      <button className="trend-toggle" onClick={() => { setTooltip(null); onToggle() }} aria-expanded={expanded} aria-controls={bodyId}>
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><path d="M4 4v16h17M8 16v-4m5 4V6m5 10v-7" /></svg>
        <h2>最近执行</h2><span className="trend-subtitle">看变化 · 点柱子查看报告</span>
        <span className="trend-fold">{expanded ? '收起' : '展开'} <span aria-hidden="true">{expanded ? '⌃' : '⌄'}</span></span>
      </button>
      {expanded && <div className="trend-legend" aria-label="结果图例">{outcomes.map(item => <span key={item.key}><i className={`trend-swatch ${item.className}`} />{item.label}</span>)}<span><i className="trend-incomplete-symbol">◇</i>未正常完成</span></div>}
    </div>
    {expanded && <div id={bodyId} className="trend-body">
      <div className="trend-controls">
        <label>套件<WorkbenchSelect ariaLabel="趋势图测试套件" value={suiteId} options={suites.map(suite => ({ value: suite.id, label: suite.name }))} onChange={onSuiteChange} /></label>
        <label className="trend-range">比较范围<WorkbenchSelect ariaLabel="趋势图比较范围" value={group?.key ?? ''} disabled={groups.length === 0}
          options={groups.length === 0 ? [{ value: '', label: '暂无执行记录' }] : groups.map(item => ({ value: item.key, label: `${item.key === 'unrecorded' ? '旧记录 · 范围未保存' : `相同 ${item.latest.case_selection!.cases.length} 条用例 · #${item.latest.id.slice(0, 8)}`} · ${item.runs.length} 次` }))}
          onChange={value => { setRangeKey(value); setTooltip(null) }} /></label>
        <span className="trend-sample">{runs.length} 次执行 <span>旧 → 新</span></span>
      </div>
      {error ? <div className="trend-message" role="alert">图表读取失败：{error}<button onClick={() => setRetry(value => value + 1)}>重新读取</button></div> : !current ? <div className="trend-message" role="status">正在读取实际执行记录…</div> : runs.length === 0 ? <div className="trend-message">这个套件还没有执行记录。提交测试任务后，图表会展示实际结果。</div> : <div className="trend-plot" aria-busy={loading}>
        <div className="trend-axis" aria-hidden="true"><span>{hasReportedCounts ? `${maxCount} 条` : '条数'}</span><span>{hasReportedCounts && maxCount > 1 ? Math.floor(maxCount / 2) : ''}</span><span>0</span></div>
        <div className="trend-scroll">
          <div className="trend-bars" style={{ gridTemplateColumns: `repeat(${runs.length}, minmax(${runs.length <= 12 ? 42 : 26}px, 1fr))` }}>
            {runs.map((run, index) => {
              const known = hasCounts(run)
              const countsLabel = known ? outcomes.map(item => `${item.label} ${run[item.key]}`).join('，') : '报告计数未生成或不完整'
              return <button key={run.id} className={`trend-bar ${selectedId === run.id ? 'is-selected' : ''} ${unfinished(run) ? 'is-incomplete' : ''}`}
                data-run-id={run.id} data-report-count={known ? run.total_count : undefined}
                aria-label={`${suiteName}，${timestamp(run.created_at)}，${labels[run.status]}，${countsLabel}，打开任务 ${run.id.slice(0, 8)}`}
                aria-describedby={focusedRun?.id === run.id ? tooltipId : undefined} aria-pressed={selectedId === run.id}
                onPointerEnter={event => { if (event.pointerType === 'mouse') showTooltip(run, event.currentTarget) }} onPointerLeave={event => { if (!(document.activeElement === event.currentTarget && event.currentTarget.matches(':focus-visible'))) setTooltip(null) }}
                onFocus={event => { if (event.target.matches(':focus-visible')) showTooltip(run, event.currentTarget) }} onBlur={() => setTooltip(null)}
                onClick={() => { setTooltip(null); onOpenRun(run.id) }}>
                <span className="trend-column" aria-hidden="true">
                  {unfinished(run) && <span className="trend-incomplete-symbol" title={labels[run.status]}>◇</span>}
                  {known && run.total_count! > 0 ? <span className="trend-stack" style={{ height: `${run.total_count! / maxCount * 100}%` }}>{outcomes.map(item => run[item.key]! > 0 && <span key={item.key} className={`trend-segment ${item.className}`} data-count={run[item.key]} style={{ flex: run[item.key]! }} />)}</span> : <span className={`trend-no-count ${known ? 'zero' : ''}`}>{known ? '0' : '—'}</span>}
                </span>
                <span className="trend-tick" aria-hidden="true">{runs.length <= 12 ? shortTime(run.created_at) : index + 1}</span>
              </button>
            })}
          </div>
        </div>
      </div>}
      <div className="trend-caption">
        <span>{group?.key === 'unrecorded' ? '旧记录未保存用例范围，不能确认每次执行了相同用例。' : '按保存的用例 ID 分组；◇ 为未正常完成，部分通过不代表全部通过。'}</span>
        <span title={current ? `本次读取 ${timestamp(current.readAt)}。历史搜索、状态筛选和翻页不改变图表范围。` : undefined}>{current ? `该套件最近 ${current.list.items.length} / ${current.list.matched_total} 个任务 · 组内最多 20 次` : '每个套件读取最近 100 个任务'}</span>
      </div>
    </div>}
    {focusedRun && tooltip && createPortal(<div id={tooltipId} role="tooltip" className="trend-tooltip" style={{ left: tooltip.left, top: tooltip.top, width: tooltip.width }}>
      <div className="trend-tooltip-heading"><strong>{suiteName}</strong><span className={`trend-task-status ${focusedRun.status}`}>{labels[focusedRun.status]}</span></div>
      <p className="trend-tooltip-id">#{focusedRun.id.slice(0, 8)} · {timestamp(focusedRun.created_at)}</p>
      <p>{focusedRun.case_selection ? `${focusedRun.case_selection.mode === 'all' ? '全部' : '已选'} ${focusedRun.case_selection.cases.length} 条用例` : '旧记录：执行范围未保存'}</p>
      <dl>{outcomes.map(item => <div key={item.key}><dt><i className={`trend-swatch ${item.className}`} />{item.label}</dt><dd>{focusedRun[item.key] ?? '未生成'}</dd></div>)}</dl>
      <p>报告总计 <strong>{focusedRun.total_count ?? '未生成'}</strong>{focusedRun.total_count !== null && ' 条'} · 超时设置 {focusedRun.timeout_seconds}s</p>
      {!hasCounts(focusedRun) && <p className="trend-tooltip-note">报告计数未生成或不完整，柱高不表示 0 条用例。</p>}
      {unfinished(focusedRun) && <p className="trend-tooltip-note">{['queued', 'running', 'cancelling', 'recovering'].includes(focusedRun.status) ? '任务尚未结束，报告可能还未生成。' : '任务未正常完成，已有通过结果只代表执行到的部分。'}</p>}
      <p className="trend-tooltip-action">点击柱子查看报告、日志与证据 →</p>
    </div>, document.body)}
  </section>
}
