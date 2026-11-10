import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { api, ApiError, type CaseChoice, type Detail, type Log, type Run, type RunList, type Status, type Suite, type System } from './api'
import RunReport from './RunReport'
import DatabaseView from './DatabaseView'
import CasePicker from './CasePicker'
import RunInspector from './RunInspector'
import EnvironmentPanel from './EnvironmentPanel'
import RunTrend from './RunTrend'
import WorkbenchSelect from './WorkbenchSelect'
import { readReportLocation, reportUrl, writeReportLocation } from './reportLinks'

const labels: Record<Status, string> = { queued: '待执行', running: '执行中', cancelling: '取消中', recovering: '恢复中', passed: '已通过', failed: '测试失败', error: '执行异常', timed_out: '执行超时', cancelled: '已取消' }
const active = (status: Status) => ['queued', 'running', 'cancelling', 'recovering'].includes(status)
interface Submission { key: string; suite_id: string; timeout_seconds: number; case_ids?: string[] | null; catalog_revision?: string; rerun_of_id?: string; acknowledged: boolean }
function savedSubmission(): Submission | null {
  try { const value = JSON.parse(sessionStorage.getItem('probe.submission') ?? 'null'); return value && typeof value.key === 'string' && typeof value.suite_id === 'string' && typeof value.timeout_seconds === 'number' ? value : null }
  catch { return null }
}
function savedSuite(): string {
  try { return sessionStorage.getItem('probe.selected-suite') || savedSubmission()?.suite_id || 'stage1' }
  catch { return 'stage1' }
}
function savedCaseChoices(): Record<string, CaseChoice> {
  try {
    const cached = JSON.parse(sessionStorage.getItem('probe.case-choices') ?? 'null')
    const last = savedSubmission()
    const choices = cached ?? (last ? { [last.suite_id]: { ids: last.case_ids ?? null, revision: last.catalog_revision } } : {})
    if (typeof choices !== 'object' || Array.isArray(choices)) return {}
    return Object.fromEntries(Object.entries(choices).filter(([, value]) => {
      const choice = value as CaseChoice | null
      return choice && typeof choice === 'object'
        && (choice.ids === null || (Array.isArray(choice.ids) && choice.ids.length > 0 && choice.ids.every(id => typeof id === 'string' && id.length > 0)))
        && (choice.revision === undefined || (typeof choice.revision === 'string' && /^[a-f0-9]{64}$/.test(choice.revision)))
        && (choice.count === undefined || (Number.isInteger(choice.count) && choice.count > 0))
    })) as Record<string, CaseChoice>
  } catch { return {} }
}
const time = (value: string | null) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—'
const duration = (run: Run) => run.started_at && run.finished_at
  ? `${((new Date(run.finished_at).getTime() - new Date(run.started_at).getTime()) / 1000).toFixed(2)} s` : '—'

function Icon({ name }: { name: 'play' | 'refresh' | 'arrow' | 'check' | 'code' | 'download' | 'copy' | 'clock' | 'radar' | 'database' | 'expand' }) {
  const paths = {
    play: <path d="m9 5 11 7-11 7V5Z" />,
    refresh: <><path d="M20 7v5h-5M4 17v-5h5" /><path d="M6.2 7a7 7 0 0 1 11.6-1L20 9M4 15l2.2 3A7 7 0 0 0 18 17" /></>,
    arrow: <><path d="M5 12h14m-5-5 5 5-5 5" /></>,
    check: <path d="m5 12 4 4L19 6" />,
    code: <><path d="m8 7-5 5 5 5m8-10 5 5-5 5m-3-12-2 14" /></>,
    download: <><path d="M12 3v12m-4-4 4 4 4-4M5 17v4h14v-4" /></>,
    copy: <><rect x="8" y="8" width="12" height="12" rx="2" /><path d="M16 8V4H4v12h4" /></>,
    clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
    database: <><ellipse cx="12" cy="5" rx="8" ry="3" /><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3" /></>,
    radar: <><circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="5" /><path d="M12 12 18.5 5.5" /><circle cx="12" cy="12" r="1" fill="currentColor" stroke="none" /></>,
    expand: <path d="M8 3H3v5m13-5h5v5M3 16v5h5m13-5v5h-5" />,
  }
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name]}</svg>
}

function Badge({ status }: { status: Status }) {
  return <span className={`badge ${status}`}><span className="dot" />{labels[status]}</span>
}

export default function App() {
  const [view, setView] = useState<'runs' | 'data'>(() => readReportLocation().view)
  const [suites, setSuites] = useState<Suite[]>([])
  const [suiteId, setSuiteId] = useState(savedSuite)
  const [caseChoices, setCaseChoices] = useState<Record<string, CaseChoice>>(savedCaseChoices)
  const [pickerOpen, setPickerOpen] = useState(false)
  const [inspectorOpen, setInspectorOpen] = useState(false)
  const [environmentOpen, setEnvironmentOpen] = useState(false)
  const [focusResults, setFocusResults] = useState(false)
  const [trendExpanded, setTrendExpanded] = useState(true)
  const [list, setList] = useState<RunList | null>(null)
  const [historyDraft, setHistoryDraft] = useState('')
  const [historySearch, setHistorySearch] = useState('')
  const [historySuite, setHistorySuite] = useState('')
  const [historyStatus, setHistoryStatus] = useState('')
  const [historyOffset, setHistoryOffset] = useState(0)
  const [historyLoading, setHistoryLoading] = useState(false)
  const [selected, setSelected] = useState<string | null>(() => readReportLocation().runId)
  const [detail, setDetail] = useState<Detail | null>(null)
  const [log, setLog] = useState<Log>({ content: '', truncated: false })
  const [tab, setTab] = useState<'report' | 'log'>(() => readReportLocation().tab)
  const [submitting, setSubmitting] = useState(false)
  const [rerunning, setRerunning] = useState(false)
  const [timeoutSeconds, setTimeoutSeconds] = useState(60)
  const [lastSubmission, setLastSubmission] = useState<Submission | null>(savedSubmission)
  const [submissionNotice, setSubmissionNotice] = useState<{ key: string; message: string } | null>(null)
  const [submissionError, setSubmissionError] = useState<{ message: string; refreshCases: boolean } | null>(null)
  const [noticePaused, setNoticePaused] = useState(false)
  const [system, setSystem] = useState<System | null>(null)
  const [cancelling, setCancelling] = useState(false)
  const [logAttempt, setLogAttempt] = useState<string | null>(null)
  const [httpServiceId, setHttpServiceId] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [detailError, setDetailError] = useState('')
  const [reportMissing, setReportMissing] = useState(false)
  const [updated, setUpdated] = useState<string | null>(null)
  const [copiedId, setCopiedId] = useState<string | null>(null)
  const [copiedLink, setCopiedLink] = useState<string | null>(null)
  const [suiteInfo, setSuiteInfo] = useState<{ suite: Suite; left: number; top: number; width: number } | null>(null)
  const suiteInfoOpen = suiteInfo !== null
  const suiteInfoAnchor = useRef<HTMLElement | null>(null)
  const suiteInfoRef = useRef<HTMLDivElement>(null)
  const suiteOptionsRef = useRef<HTMLDivElement>(null)
  const suiteInfoCloseTimer = useRef<number | null>(null)
  const suiteInfoKeyboardFocus = useRef(false)
  const suiteInfoPointerType = useRef('mouse')
  const selectedRef = useRef(selected)
  selectedRef.current = selected
  const historyRequest = useRef<AbortController | null>(null)
  const historyParams = new URLSearchParams({ limit: '10', offset: String(historyOffset) })
  if (historySearch) historyParams.set('q', historySearch)
  if (historySuite) historyParams.set('suite_id', historySuite)
  if (historyStatus) historyParams.set('status', historyStatus)
  const historyQuery = historyParams.toString()

  useEffect(() => { writeReportLocation(selected, tab, view) }, [selected, tab, view])
  useEffect(() => {
    const restore = () => {
      const location = readReportLocation()
      hideSuiteInfo(); setView(location.view); setSelected(location.runId); setTab(location.tab)
      setLogAttempt(null); setHttpServiceId(null); setFocusResults(false); setInspectorOpen(false); setPickerOpen(false); setEnvironmentOpen(false)
    }
    window.addEventListener('popstate', restore)
    return () => window.removeEventListener('popstate', restore)
  }, [])

  // 标签页内保留选择；后台刷新不会重置，缓存中的用例版本仍由后端校验。
  useEffect(() => { try { sessionStorage.setItem('probe.selected-suite', suiteId) } catch { /* 存储受限时仍可在当前页面选择。 */ } }, [suiteId])
  useEffect(() => { try { sessionStorage.setItem('probe.case-choices', JSON.stringify(caseChoices)) } catch { /* 保留内存中的选择。 */ } }, [caseChoices])
  useEffect(() => {
    if (suites.length > 0 && !suites.some(suite => suite.id === suiteId)) setSuiteId(suites.find(suite => suite.id === 'stage1')?.id ?? suites[0].id)
  }, [suites, suiteId])

  useEffect(() => { setSubmissionError(null) }, [suiteId])

  // 从图表选择下方套件时，也把左栏的选中项滚动到可见位置；只滚动列表。
  useEffect(() => {
    const container = suiteOptionsRef.current
    const chosen = container?.querySelector<HTMLElement>('.suite-option.chosen')
    if (!container || !chosen) return
    const viewport = container.getBoundingClientRect(), item = chosen.getBoundingClientRect()
    if (item.bottom > viewport.bottom) container.scrollTop += item.bottom - viewport.bottom
    else if (item.top < viewport.top) container.scrollTop -= viewport.top - item.top
  }, [suiteId, suites.length, view, focusResults])

  // 提交反馈放到独立浮层，不占套件列表高度；每次重放也重新开始计时。
  useEffect(() => {
    if (!submissionNotice || noticePaused) return
    const timer = window.setTimeout(() => setSubmissionNotice(null), 8000)
    return () => window.clearTimeout(timer)
  }, [submissionNotice, noticePaused])

  useEffect(() => {
    if (!focusResults) return
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape' && !inspectorOpen && !pickerOpen && !environmentOpen) setFocusResults(false) }
    window.addEventListener('keydown', escape)
    return () => window.removeEventListener('keydown', escape)
  }, [focusResults, inspectorOpen, pickerOpen, environmentOpen])

  useEffect(() => {
    const timer = window.setTimeout(() => { setHistorySearch(historyDraft.trim()); setHistoryOffset(0) }, 350)
    return () => window.clearTimeout(timer)
  }, [historyDraft])

  const refresh = useCallback(async (background = false) => {
    // 自动刷新保留当前页文字和按钮状态，不中断尚未完成的前台读取。
    if (background && historyRequest.current) return
    historyRequest.current?.abort()
    const controller = new AbortController()
    historyRequest.current = controller
    if (!background) setHistoryLoading(true)
    try {
      const options = { signal: controller.signal }
      const [data, available, runtime] = await Promise.all([api<RunList>(`/runs?${historyQuery}`, options), api<Suite[]>('/suites', options), api<System>('/system', options)])
      if (controller.signal.aborted) return
      const lastPage = Math.max(0, Math.floor((data.matched_total - 1) / data.limit) * data.limit)
      if (data.offset > lastPage) { setHistoryOffset(lastPage); return }
      setList(data); setSuites(available); setSystem(runtime); setError(''); setUpdated(new Date().toISOString())
      if (selectedRef.current === null && data.items.length > 0) setSelected(data.items[0].id)
    } catch (e) { if (!controller.signal.aborted) setError(e instanceof Error ? e.message : '无法连接平台 API') }
    finally { if (historyRequest.current === controller) { historyRequest.current = null; if (!controller.signal.aborted) setHistoryLoading(false) } }
  }, [historyQuery])

  useEffect(() => {
    void refresh()
    const interval = window.setInterval(() => void refresh(true), 2000)
    return () => { window.clearInterval(interval); historyRequest.current?.abort() }
  }, [refresh])

  function clearSuiteInfoTimer() {
    if (suiteInfoCloseTimer.current !== null) window.clearTimeout(suiteInfoCloseTimer.current)
    suiteInfoCloseTimer.current = null
  }
  function showSuiteInfo(suite: Suite, anchor: HTMLElement) {
    clearSuiteInfoTimer()
    suiteInfoAnchor.current = anchor
    const rect = anchor.getBoundingClientRect()
    const width = Math.min(320, window.innerWidth - 24)
    const beside = rect.right + width + 20 <= window.innerWidth
    const left = beside ? rect.right + 10 : Math.max(12, Math.min(rect.left, window.innerWidth - width - 12))
    const top = Math.max(12, Math.min(beside ? rect.top : rect.bottom + 8, window.innerHeight - 230))
    setSuiteInfo({ suite, left, top, width })
  }
  function hideSuiteInfo() {
    clearSuiteInfoTimer(); setSuiteInfo(null)
  }
  function hideSuiteInfoSoon() {
    clearSuiteInfoTimer()
    suiteInfoCloseTimer.current = window.setTimeout(() => {
      suiteInfoCloseTimer.current = null; setSuiteInfo(null)
    }, 180)
  }
  useEffect(() => () => clearSuiteInfoTimer(), [])
  useEffect(() => {
    if (!suiteInfoOpen) return
    const closeOutside = (event: PointerEvent) => {
      if (!suiteInfoRef.current?.contains(event.target as Node) && !suiteInfoAnchor.current?.contains(event.target as Node)) hideSuiteInfo()
    }
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') hideSuiteInfo()
    }
    const closeOnScroll = (event: Event) => { if (!suiteInfoRef.current?.contains(event.target as Node)) hideSuiteInfo() }
    window.addEventListener('resize', hideSuiteInfo)
    document.addEventListener('scroll', closeOnScroll, true)
    document.addEventListener('pointerdown', closeOutside)
    document.addEventListener('keydown', closeOnEscape)
    return () => {
      window.removeEventListener('resize', hideSuiteInfo)
      document.removeEventListener('scroll', closeOnScroll, true)
      document.removeEventListener('pointerdown', closeOutside)
      document.removeEventListener('keydown', closeOnEscape)
    }
  }, [suiteInfoOpen])

  useEffect(() => {
    if (!selected) { setDetail(null); return }
    const id = selected
    let alive = true, fetching = false, timer: number | null = null
    const controller = new AbortController()
    setDetail(null); setLog({ content: '', truncated: false }); setDetailError(''); setReportMissing(false)
    async function load() {
      if (fetching) return
      fetching = true
      try {
        const options = { signal: controller.signal }
        const logPath = httpServiceId ? `/runs/${id}/http-services/${httpServiceId}/log` : `/runs/${id}/log${logAttempt ? `?attempt_id=${logAttempt}` : ''}`
        const [data, output] = await Promise.all([api<Detail>(`/runs/${id}`, options), api<Log>(logPath, options)])
        if (alive && selectedRef.current === id) {
          setDetail(data); setLog(output); setDetailError('')
          if (active(data.status)) timer = window.setTimeout(() => void load(), 1000)
        }
      } catch (e) { if (alive) {
        const missing = e instanceof ApiError && e.status === 404 && e.message === 'Test run does not exist'
        setReportMissing(missing); setDetailError(missing ? '这条任务记录不存在，报告链接可能已失效。' : e instanceof Error ? e.message : '详情加载失败')
        if (!(e instanceof ApiError && e.status === 404)) timer = window.setTimeout(() => void load(), 2000)
      } }
      finally { fetching = false }
    }
    void load()
    return () => { alive = false; controller.abort(); if (timer !== null) window.clearTimeout(timer) }
  }, [selected, logAttempt, httpServiceId])

  async function executeSubmission(submission: Submission) {
    const pending = { ...submission, acknowledged: false }
    setLastSubmission(pending); sessionStorage.setItem('probe.submission', JSON.stringify(pending))
    const payload = { suite_id: submission.suite_id, timeout_seconds: submission.timeout_seconds, case_ids: submission.case_ids ?? null, catalog_revision: submission.catalog_revision }
    const run = await api<Run>(submission.rerun_of_id ? `/runs/${submission.rerun_of_id}/rerun` : '/runs', {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': submission.key }, body: submission.rerun_of_id ? undefined : JSON.stringify(payload),
    })
    const saved = { ...pending, acknowledged: true }
    setLastSubmission(saved); sessionStorage.setItem('probe.submission', JSON.stringify(saved))
    setNoticePaused(false)
    setSubmissionNotice({ key: crypto.randomUUID(), message: run.replayed ? '已复用原任务：相同提交键没有新增执行。' : submission.rerun_of_id ? `已重新执行：#${run.id.slice(0, 8)}，原任务结果保留。` : `任务已提交：#${run.id.slice(0, 8)}，执行状态见历史记录。` })
    openRun(run.id); await refresh()
  }

  async function submit(replay = false) {
    setSubmitting(true); setSubmissionError(null); setSubmissionNotice(null)
    try {
      if (replay && lastSubmission) { await executeSubmission(lastSubmission); return }
      const choice = caseChoices[suiteId]
      const payload = { suite_id: suiteId, timeout_seconds: timeoutSeconds, case_ids: choice?.ids ?? null, catalog_revision: choice?.revision }
      const sameCases = lastSubmission && JSON.stringify([...(lastSubmission.case_ids ?? [])].sort()) === JSON.stringify([...(payload.case_ids ?? [])].sort())
      const reuse = lastSubmission && !lastSubmission.rerun_of_id && !lastSubmission.acknowledged && lastSubmission.suite_id === payload.suite_id && lastSubmission.timeout_seconds === payload.timeout_seconds && sameCases
      const submission: Submission = { ...payload, key: reuse ? lastSubmission!.key : crypto.randomUUID(), acknowledged: false }
      await executeSubmission(submission)
    } catch (e) {
      const changedCases = e instanceof ApiError && e.status === 422 && /Case list changed|Selected cases must exist/.test(e.message)
      setSubmissionError({ message: changedCases ? '用例文件已更新。请重新读取用例列表，确认选择后再提交。' : `任务提交失败：${e instanceof Error ? e.message : '请重新检查提交参数。'}`, refreshCases: changedCases })
    }
    finally { setSubmitting(false) }
  }

  async function rerunSelected() {
    if (!detail || active(detail.status)) return
    setRerunning(true); setDetailError('')
    try {
      const reuse = lastSubmission && !lastSubmission.acknowledged && lastSubmission.rerun_of_id === detail.id
      await executeSubmission(reuse ? lastSubmission : { key: crypto.randomUUID(), suite_id: detail.suite_id, timeout_seconds: detail.timeout_seconds, case_ids: detail.case_selection?.cases.map(test => test.id) ?? null, rerun_of_id: detail.id, acknowledged: false })
    } catch (e) { setDetailError(e instanceof Error ? e.message : '重新执行失败') }
    finally { setRerunning(false) }
  }

  async function cancelSelected() {
    if (!selected) return
    setCancelling(true); setDetailError('')
    try { const data = await api<Detail>(`/runs/${selected}/cancel`, { method: 'POST' }); setDetail(data); await refresh() }
    catch (e) { setDetailError(e instanceof Error ? e.message : '取消失败') }
    finally { setCancelling(false) }
  }

  async function copyRunId() {
    if (!detail) return
    try { await navigator.clipboard.writeText(detail.id); setCopiedId(detail.id) }
    catch { setDetailError('复制失败，可以在执行上下文中手动选择任务 ID。') }
  }

  async function copyReportLink() {
    if (!detail) return
    try { await navigator.clipboard.writeText(reportUrl(detail.id)); setCopiedLink(detail.id) }
    catch { setDetailError('复制链接失败，可以从浏览器地址栏复制当前任务地址。') }
  }

  async function openLatestRun() {
    try {
      const latest = await api<RunList>('/runs?limit=1&offset=0')
      clearHistoryFilters()
      if (latest.items[0]) openRun(latest.items[0].id)
      else { setSelected(null); setDetailError(''); setReportMissing(false) }
    } catch (e) { setDetailError(e instanceof Error ? e.message : '无法读取最近任务') }
  }

  function openRun(runId: string) {
    writeReportLocation(runId, 'report', 'runs', false)
    setView('runs')
    setSelected(runId); setLogAttempt(null); setHttpServiceId(null); setTab('report')
    if (window.innerWidth <= 620) {
      const reducedMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
      document.querySelector('.detail-card')?.scrollIntoView({ behavior: reducedMotion ? 'auto' : 'smooth', block: 'start' })
    }
  }

  function navigate(value: 'runs' | 'data') {
    hideSuiteInfo(); setView(value); setFocusResults(false); setInspectorOpen(false)
    window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}${value === 'data' ? '#data' : ''}`)
    window.scrollTo(0, 0)
  }

  const suiteName = (id: string) => suites.find(s => s.id === id)?.name ?? id
  const selectedSuite = suites.find(s => s.id === suiteId)
  const caseChoice = caseChoices[suiteId] ?? { ids: null }
  const pending = list ? list.summary.queued + list.summary.running + list.summary.cancelling + list.summary.recovering : null
  const failures = list ? list.summary.failed + list.summary.error + list.summary.timed_out : null
  function clearHistoryFilters() { setHistoryDraft(''); setHistorySearch(''); setHistorySuite(''); setHistoryStatus(''); setHistoryOffset(0) }
  function locateSelected() { if (selected) { setHistorySuite(''); setHistoryStatus(''); setHistoryOffset(0); setHistoryDraft(selected); setHistorySearch(selected) } }

  return <div className="app-shell">
    <aside className="sidebar" aria-label="工作台导航">
      <a className="brand" href="/" aria-label="Probe 首页" title="Probe 首页">
        <span className="brand-mark"><Icon name="radar" /></span>
        <span className="brand-word">Probe<span className="brand-point">.</span></span>
      </a>
      <nav className="side-nav">
        <button className={`nav-item ${view === 'runs' ? 'selected' : ''}`} onClick={() => navigate('runs')} title="测试工作台" aria-label="测试工作台"><Icon name="play" /><span className="nav-text">测试</span></button>
        <button className={`nav-item ${view === 'data' ? 'selected' : ''}`} onClick={() => navigate('data')} title="数据浏览" aria-label="数据浏览"><Icon name="database" /><span className="nav-text">数据</span></button>
        <a className="nav-item" href="http://127.0.0.1:8002/docs" target="_blank" rel="noreferrer" title="订单 API 文档" aria-label="订单 API 文档"><Icon name="code" /><span className="nav-text">API</span></a>
      </nav>
      <div className="sidebar-bottom"><span className="stage-label" title="阶段三">03</span><span className={`dot ${error || system?.online_workers === 0 ? 'offline' : 'online'}`} title={system ? `${system.online_workers} 个 Worker 在线` : '连接中'} /></div>
    </aside>

    <main className={`workspace ${view === 'data' ? 'data-workspace' : focusResults ? 'results-focused' : 'with-run-trend'}`}>
      {view === 'data' ? <DatabaseView suites={suites} onInspectRun={id => { navigate('runs'); openRun(id) }} /> : <>
      <header className="page-header">
        <div className="page-heading"><div className="eyebrow"><span>PROBE</span><span className="breadcrumb-slash">/</span>EXECUTION WORKSPACE</div><h1>测试工作台</h1></div>
        <div className="header-tools"><span className="local-tag">LOCAL</span><button className={`connection environment-trigger ${error || system?.online_workers === 0 ? 'disconnected' : ''}`} onClick={() => { hideSuiteInfo(); setEnvironmentOpen(true) }} aria-label="查看运行环境检查" title="查看数据库、Worker 和报告目录的实际状态"><span className="dot" />{error ? '连接异常' : system ? `${system.online_workers} 个 Worker 在线` : '连接中'}<span aria-hidden="true">⌄</span></button><a className="doc-link" href="http://127.0.0.1:8001/docs" target="_blank" rel="noreferrer">平台 API <span>↗</span></a></div>
      </header>

      {error && <div className="alert workspace-alert" role="alert">无法读取平台状态：{error}<button onClick={() => void refresh()}>重新读取</button></div>}
      {!error && system?.online_workers === 0 && <div className="worker-note workspace-alert">Worker 暂时离线，任务将保留在队列中，启动后继续执行。</div>}

      <section className="stats" aria-label="实际任务统计">
        <div className="stat"><span>累计任务</span><strong>{list?.total ?? '—'}</strong></div>
        <div className="stat"><span className="stat-dot blue" /><span>等待 / 执行</span><strong>{pending ?? '—'}</strong></div>
        <div className="stat"><span className="stat-dot green" /><span>通过</span><strong>{list?.summary.passed ?? '—'}</strong></div>
        <div className="stat"><span className="stat-dot red" /><span>失败 / 异常</span><strong>{failures ?? '—'}</strong><small>{list ? `${list.summary.cancelled} 已取消` : ''}</small></div>
      </section>

      <div className="workbench-layout">
        <div className="explorer">
          <section className="submit-card">
            <div className="section-heading"><div><Icon name="code" /><h2>测试套件</h2></div><span className="pane-label">SUITES</span></div>
            <div className="suite-options" ref={suiteOptionsRef}>
              {suites.map(suite => <button key={suite.id} className={`suite-option ${suiteId === suite.id ? 'chosen' : ''}`} onClick={() => { setSuiteId(suite.id); if (suiteInfoPointerType.current === 'touch') { if (suiteInfo?.suite.id === suite.id) hideSuiteInfo(); else showSuiteInfo(suite, suiteInfoAnchor.current!) } }} aria-pressed={suiteId === suite.id}
                aria-describedby={suiteInfo?.suite.id === suite.id ? 'suite-description-popover' : undefined}
                onPointerEnter={event => { if (event.pointerType === 'mouse') { suiteInfoKeyboardFocus.current = false; showSuiteInfo(suite, event.currentTarget) } }}
                onPointerLeave={event => { if (event.pointerType === 'mouse' && !suiteInfoKeyboardFocus.current) hideSuiteInfoSoon() }}
                onPointerDown={event => { suiteInfoPointerType.current = event.pointerType; suiteInfoAnchor.current = event.currentTarget; if (event.pointerType === 'mouse') suiteInfoKeyboardFocus.current = false }}
                onFocus={event => { if (event.target.matches(':focus-visible')) { suiteInfoKeyboardFocus.current = true; showSuiteInfo(suite, event.currentTarget) } }}
                onBlur={event => { if (!suiteInfoRef.current?.contains(event.relatedTarget as Node)) { suiteInfoKeyboardFocus.current = false; hideSuiteInfoSoon() } }}
              >
                <span className="suite-glyph"><Icon name="code" /></span>
                <span className="suite-copy"><strong>{suite.name}</strong><small>{suite.id}</small></span>
                {suite.requires_postgres && <span className="suite-tag">PG</span>}{suite.expected_failure && <span className="demo-tag" title="套件包含故意失败的演示用例">含故障</span>}
                <span className="radio-mark">{suiteId === suite.id && <span />}</span>
              </button>)}
              {suites.length === 0 && <p className="suite-loading">正在读取测试套件…</p>}
            </div>
            <button className="case-selection-trigger" onClick={() => { hideSuiteInfo(); setPickerOpen(true) }} disabled={!selectedSuite}><span>执行范围 <strong>{caseChoice.ids ? `已选 ${caseChoice.ids.length} 条` : caseChoice.count ? `全部 ${caseChoice.count} 条` : '全部用例'}</strong></span><span>查看 / 选择 →</span></button>
            <div className="execution-controls"><label htmlFor="timeout-input"><Icon name="clock" />每次尝试超时</label><div className="timeout-field"><input id="timeout-input" aria-label="执行超时秒数" type="number" min="1" max="300" value={timeoutSeconds} onChange={event => setTimeoutSeconds(Number(event.target.value))} /><span>s</span></div></div>
            <div className="submit-footer"><button className="primary-button" onClick={() => void submit()} disabled={submitting || rerunning || !selectedSuite || !!error || !Number.isInteger(timeoutSeconds) || timeoutSeconds < 1 || timeoutSeconds > 300}><Icon name="play" /><span>{submitting ? '正在提交…' : '提交测试任务'}</span><Icon name="arrow" /></button><button className="secondary-button" aria-label="重放上次提交" onClick={() => void submit(true)} disabled={!lastSubmission || submitting || rerunning || !!error} title="复用上次的套件、超时和提交键"><Icon name="refresh" /></button></div>
          </section>

          <section className="runs-card">
            <div className="section-heading"><div><Icon name="clock" /><h2>执行历史</h2><span className="history-count" title="符合筛选的任务数">{list?.matched_total ?? '—'}</span></div><button className="icon-button" aria-label="刷新任务" onClick={() => void refresh()}><Icon name="refresh" /></button></div>
            <div className="history-filters"><div className="history-search"><input aria-label="搜索执行历史" placeholder="搜索任务 ID 或套件名称" value={historyDraft} maxLength={100} onChange={event => setHistoryDraft(event.target.value)} /><button onClick={clearHistoryFilters} aria-label="清除历史筛选" title="清除搜索与筛选">×</button></div><details className="history-filter-details"><summary aria-label="展开历史筛选" title={[historySuite ? suiteName(historySuite) : '全部套件', historyStatus ? labels[historyStatus as Status] : '全部状态'].join(' / ')}>筛选{historySuite || historyStatus ? ` · ${Number(!!historySuite) + Number(!!historyStatus)}` : ''}</summary><div className="history-selects"><label>测试套件<WorkbenchSelect ariaLabel="按套件筛选历史" value={historySuite} options={[{ value: '', label: '全部套件' }, ...suites.map(suite => ({ value: suite.id, label: suite.name }))]} onChange={value => { setHistorySuite(value); setHistoryOffset(0) }} /></label><label>执行状态<WorkbenchSelect ariaLabel="按状态筛选历史" value={historyStatus} options={[{ value: '', label: '全部状态' }, ...Object.entries(labels).map(([value, label]) => ({ value, label }))]} onChange={value => { setHistoryStatus(value); setHistoryOffset(0) }} /></label><button onClick={event => { event.currentTarget.closest('details')!.open = false }}>收起筛选</button></div></details></div>
            <div className="run-list" aria-busy={historyLoading}>
              {list?.items.map(run => <button key={run.id} className={`run-item ${selected === run.id ? 'is-selected' : ''}`} onClick={() => openRun(run.id)}>
                <div className="run-item-top"><strong>{suiteName(run.suite_id)}</strong><Badge status={run.status} /></div>
                <div className="run-item-bottom"><span className="run-id">#{run.id.slice(0, 8)}</span><span>{new Date(run.created_at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false })}</span><span className="run-case-count">{run.total_count === null ? active(run.status) ? '待生成' : '无报告' : `${run.passed_count}/${run.total_count} 通过`}</span></div>
              </button>)}
              {list?.items.length === 0 && <div className="empty-state history-empty"><Icon name="clock" /><strong>{list.total === 0 ? '还没有执行记录' : '没有符合筛选的任务'}</strong><p>{list.total === 0 ? '提交左上方的套件开始测试。' : '清除筛选或换一个搜索条件。'}</p></div>}
            </div>
            <div className="history-pagination"><button aria-label="历史上一页" disabled={historyLoading || historyOffset === 0} onClick={() => setHistoryOffset(value => Math.max(0, value - 10))}>←</button><span>{list ? `${list.matched_total === 0 ? 0 : Math.floor(list.offset / list.limit) + 1} / ${Math.ceil(list.matched_total / list.limit)} 页 · 每页 ${list.limit} 条` : historyLoading ? '读取中…' : '等待历史记录'}</span><button aria-label="历史下一页" disabled={historyLoading || !list || list.offset + list.items.length >= list.matched_total} onClick={() => setHistoryOffset(value => value + 10)}>→</button></div>
          </section>
        </div>

        <div className="results-column">
        <RunTrend suites={suites} suiteId={suiteId} onSuiteChange={id => { hideSuiteInfo(); setSuiteId(id) }} refreshToken={updated} expanded={trendExpanded} enabled={!focusResults && !!selectedSuite} selectedId={selected} onToggle={() => setTrendExpanded(value => !value)} onOpenRun={openRun} />
        <section className="detail-card">
          <div className="section-heading detail-heading"><div><Icon name="code" /><h2>结果与证据</h2>{detail && <Badge status={detail.status} />}</div>{detail && <button className="focus-results-button" aria-pressed={focusResults} onClick={() => { hideSuiteInfo(); setFocusResults(value => !value) }}><Icon name="expand" />{focusResults ? '返回工作台布局' : '专注结果'}</button>}</div>
          {!selected && <div className="empty-state detail-empty"><span className="empty-icon"><Icon name="code" /></span><strong>在这里，查看测试真正做了什么</strong><p>选择一个套件并提交，或从左侧打开历史记录。<br />断言结果、执行日志和恢复记录会显示在这里。</p></div>}
          {selected && !detail && !detailError && <div className="loading-state">正在读取执行结果…</div>}
          {detailError && <div className="alert detail-alert" role="alert">{detailError}{reportMissing && <button onClick={() => void openLatestRun()}>查看最近任务</button>}</div>}
          {detail && <>
            <div className="detail-intro compact-detail-intro">
              <div className="detail-title"><div className="compact-detail-name"><h3>{suiteName(detail.suite_id)}</h3><div className="detail-run-id"><span className="run-id">RUN #{detail.id.slice(0, 8)}</span><button className="copy-button" onClick={() => void copyRunId()} aria-label="复制任务 ID" title={copiedId === detail.id ? '已复制' : '复制完整任务 ID'}><Icon name={copiedId === detail.id ? 'check' : 'copy'} /></button>{copiedId === detail.id && <span className="copied-hint">已复制</span>}</div></div><div className="detail-actions"><button className="copy-report-link" aria-label="复制报告链接" onClick={() => void copyReportLink()} title="复制可直接打开这次历史报告的本地地址"><Icon name="copy" />{copiedLink === detail.id ? '已复制链接' : '报告链接'}</button><button className="inspect-run-button" onClick={() => { hideSuiteInfo(); setInspectorOpen(true) }}>执行详情</button>{active(detail.status) ? <button className="cancel-button" onClick={() => void cancelSelected()} disabled={cancelling || detail.status === 'cancelling'}>{cancelling || detail.status === 'cancelling' ? '取消中…' : '取消任务'}</button> : <button className="rerun-button" onClick={() => void rerunSelected()} disabled={rerunning || submitting || !!error} title="复用这个任务的范围与超时，创建新任务"><Icon name="play" />{rerunning ? '提交中…' : '重新执行'}</button>}</div></div>
              <div className="run-quick-meta"><span>{detail.case_selection ? `${detail.case_selection.cases.length} 条用例` : detail.total_count !== null ? `报告 ${detail.total_count} 条` : '范围未记录'}</span><span>总历时 <code>{duration(detail)}</code></span>{(detail.http_services ?? []).length > 0 && <button className="http-service-shortcut" onClick={() => setInspectorOpen(true)}>HTTP 服务 {(detail.http_services ?? []).length} · {(detail.http_services ?? []).find(service => service.status === 'ready')?.base_url ?? '地址与日志'}</button>}<span>退出码 <code>{detail.exit_code ?? '—'}</code></span><button onClick={() => setInspectorOpen(true)}>尝试 {detail.attempt_count === 0 && detail.report ? '旧版记录' : detail.attempt_count}{detail.attempts.some(attempt => attempt.status === 'interrupted') ? ' · 含中断' : ''}</button>{['amount_bug_demo', 'failure_demo'].includes(detail.suite_id) && <button className="compact-demo-tag" onClick={() => setInspectorOpen(true)}>含故障演示</button>}{detail.rerun_of_id && <button onClick={() => openRun(detail.rerun_of_id!)}>来自 #{detail.rerun_of_id.slice(0, 8)}</button>}{list && !historyLoading && !list.items.some(run => run.id === detail.id) && <button onClick={locateSelected} title="此任务不在当前历史列表中">在历史中定位</button>}</div>
              {selectedSuite && detail.suite_id !== suiteId && <div className="report-draft-context"><span>正在查看：{suiteName(detail.suite_id)} 的历史任务 · 待执行：{selectedSuite.name}</span><button disabled={!suites.some(suite => suite.id === detail.suite_id)} onClick={() => { hideSuiteInfo(); setSuiteId(detail.suite_id) }}>切换到报告套件</button></div>}
            </div>
            {active(detail.status) && <div className="progress-note compact-progress"><span className="pulse-dot" />{detail.status === 'queued' ? '已持久保存，等待 Worker 领取。' : detail.status === 'cancelling' ? '正在停止进程并清理测试数据。' : detail.status === 'recovering' ? '正在清理旧尝试并恢复执行。' : 'pytest 正在执行，结果自动更新。'}</div>}
            {detail.error_message && !active(detail.status) && <button className={`compact-execution-note ${detail.status}`} onClick={() => setInspectorOpen(true)} title={detail.error_message}><span>{detail.error_message}</span><strong>查看详情 →</strong></button>}
            <div className="tabs"><button className={tab === 'report' ? 'current' : ''} onClick={() => setTab('report')}><Icon name="check" />测试报告{detail.total_count !== null && <span className="tab-count">{detail.total_count}</span>}</button><button className={tab === 'log' ? 'current' : ''} onClick={() => setTab('log')}><Icon name="code" />执行日志</button>{detail.report && <a href={`/api/runs/${detail.id}/junit`} className="download-link"><Icon name="download" />JUnit XML</a>}{!active(detail.status) && <a href={`/api/runs/${detail.id}/evidence`} className="download-link" title="下载本次报告、请求与数据库证据、执行日志">证据 JSON</a>}</div>
            <div className="detail-body">
              {tab === 'report' ? detail.report ? <RunReport key={detail.id} run={detail} /> : <div className="report-pending"><Icon name="code" /><p>{active(detail.status) ? '执行结束后展示实际测试结果。' : '这次执行没有可读取的测试报告，请查看执行日志。'}</p></div> : <div className="log-content">{log.truncated && <p>当前显示日志最后 256 KiB。</p>}<div className="log-window"><div className="log-toolbar"><span className="terminal-prompt">&gt;_</span><span>{httpServiceId ? 'HTTP 服务 / server.log' : 'pytest / output.log'}</span>{httpServiceId && <button className="back-to-pytest-log" onClick={() => { setLogAttempt((detail.http_services ?? []).find(service => service.id === httpServiceId)?.attempt_id ?? null); setHttpServiceId(null) }}>返回 pytest 日志</button>}{logAttempt && <span className="log-attempt-label">attempt #{logAttempt.slice(0, 8)}</span>}</div><pre>{log.content || (active(detail.status) ? '等待 pytest 输出…' : '暂无输出。')}</pre></div></div>}
            </div>
          </>}
        </section>
        </div>
      </div>
      <footer className="page-footer"><span><span className="footer-status-dot" />PostgreSQL 持久队列</span><span>{updated ? `最近同步 ${new Date(updated).toLocaleTimeString('zh-CN', { hour12: false })}` : '等待同步'}<span className="footer-separator">/</span>自动刷新 2s</span></footer>
      </>}
    </main>
    {submissionError && createPortal(<div className="submission-toast submission-toast-error" role="alert">
      <span className="submission-toast-message">{submissionError.message}</span><div className="submission-toast-actions">
        {submissionError.refreshCases && <button onClick={() => { hideSuiteInfo(); setPickerOpen(true) }}>重新选择用例</button>}
        <button className="submission-error-close" aria-label="关闭提交错误提示" onClick={() => setSubmissionError(null)}>×</button>
      </div>
    </div>, document.body)}
    {submissionNotice && createPortal(<div key={submissionNotice.key} className="submission-toast" role="status" aria-live="polite" aria-atomic="true"
      onMouseEnter={() => setNoticePaused(true)} onMouseLeave={() => setNoticePaused(false)}
      onFocusCapture={() => setNoticePaused(true)} onBlurCapture={event => { if (!event.currentTarget.contains(event.relatedTarget as Node)) setNoticePaused(false) }}
    ><span className="submission-toast-icon"><Icon name="check" /></span><span className="submission-toast-message">{submissionNotice.message}</span><button aria-label="关闭提交提示" onClick={() => setSubmissionNotice(null)}>×</button></div>, document.body)}
    {environmentOpen && <EnvironmentPanel suiteId={suiteId} onClose={() => setEnvironmentOpen(false)} />}
    {inspectorOpen && detail && <RunInspector key={detail.id} run={detail} suiteName={suiteName(detail.suite_id)} labels={labels} selectedAttempt={logAttempt} onClose={() => setInspectorOpen(false)} onAttempt={id => { setLogAttempt(id); setHttpServiceId(null); setTab('log'); setInspectorOpen(false) }} onSource={id => { openRun(id); setInspectorOpen(false) }} onHttpLog={id => { setHttpServiceId(id); setLogAttempt(null); setTab('log'); setInspectorOpen(false) }} />}
    {pickerOpen && selectedSuite && <CasePicker suite={selectedSuite} choice={caseChoice} onClose={() => setPickerOpen(false)} onApply={choice => { setCaseChoices(previous => ({ ...previous, [suiteId]: choice })); setSubmissionError(null); setPickerOpen(false) }} />}
    {suiteInfo && createPortal(<div className="suite-description-popover" id="suite-description-popover" role="region" aria-label={`${suiteInfo.suite.name}说明`} ref={suiteInfoRef} style={{ left: suiteInfo.left, top: suiteInfo.top, width: suiteInfo.width }}
      onPointerEnter={event => { if (event.pointerType === 'mouse') clearSuiteInfoTimer() }}
      onPointerLeave={event => { if (event.pointerType === 'mouse' && !suiteInfoKeyboardFocus.current) hideSuiteInfoSoon() }}
      onBlur={event => { if (!suiteInfoAnchor.current?.contains(event.relatedTarget as Node)) { suiteInfoKeyboardFocus.current = false; hideSuiteInfoSoon() } }}
    ><div className="suite-description-heading"><strong>{suiteInfo.suite.name}</strong></div><p className="suite-description">{suiteInfo.suite.description}</p></div>, document.body)}
  </div>
}
