export type ReportTab = 'report' | 'log'
export type WorkspaceView = 'runs' | 'data'
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

// 链接只定位已有任务，不提交测试。未知或格式错误的参数回到默认工作台。
export function readReportLocation() {
  const url = new URL(window.location.href)
  const value = url.searchParams.get('run')
  const runId = value && uuid.test(value) ? value.toLowerCase() : null
  const tab: ReportTab = runId && url.searchParams.get('tab') === 'log' ? 'log' : 'report'
  const view: WorkspaceView = url.hash === '#data' ? 'data' : 'runs'
  return { runId, tab, view }
}

export function reportUrl(runId: string | null, tab: ReportTab = 'report', view: WorkspaceView = 'runs') {
  const url = new URL(window.location.href)
  if (runId) url.searchParams.set('run', runId)
  else url.searchParams.delete('run')
  if (runId && tab === 'log') url.searchParams.set('tab', 'log')
  else url.searchParams.delete('tab')
  url.hash = view === 'data' ? 'data' : ''
  return url.toString()
}

export function writeReportLocation(runId: string | null, tab: ReportTab, view: WorkspaceView, replace = true) {
  const url = reportUrl(runId, tab, view)
  if (url !== window.location.href) window.history[replace ? 'replaceState' : 'pushState'](null, '', url)
}
