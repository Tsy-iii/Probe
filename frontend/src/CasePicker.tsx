import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { api, type CaseChoice, type CaseDirectory, type Suite } from './api'

// 编辑的是下一次执行的选择；点击“应用选择”之前不改变工作台的提交参数。
export default function CasePicker({ suite, choice, onApply, onClose }: { suite: Suite; choice: CaseChoice; onApply: (choice: CaseChoice) => void; onClose: () => void }) {
  const [directory, setDirectory] = useState<CaseDirectory | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  const [inspected, setInspected] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [reload, setReload] = useState(0)
  const dialog = useRef<HTMLDivElement>(null)
  const initial = useRef(choice)

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true); setError(''); setDirectory(null)
    void api<CaseDirectory>(`/suites/${suite.id}/cases`, { signal: controller.signal }).then(data => {
      if (controller.signal.aborted) return
      setDirectory(data)
      const allowed = new Set(data.cases.map(test => test.id))
      const ids = initial.current.ids === null ? [...allowed] : initial.current.ids.filter(id => allowed.has(id))
      setSelected(ids); setInspected(ids[0] ?? data.cases[0]?.id ?? null)
      if (initial.current.ids?.some(id => !allowed.has(id))) setError('部分原选用例已不存在，请核对后重新应用选择。')
    }).catch(exception => { if (!controller.signal.aborted) setError(exception instanceof Error ? exception.message : '读取用例失败') })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [suite.id, reload])

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    dialog.current?.focus()
    return () => { document.body.style.overflow = overflow; previous?.focus() }
  }, [])

  const detail = directory?.cases.find(test => test.id === inspected)
  function toggle(id: string) { setSelected(ids => ids.includes(id) ? ids.filter(item => item !== id) : [...ids, id]) }
  function keydown(event: React.KeyboardEvent<HTMLDivElement>) {
    if (event.key === 'Escape') { event.stopPropagation(); onClose() }
    if (event.key !== 'Tab' || !dialog.current) return
    const focusable = [...dialog.current.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), [tabindex="0"]')]
    if (!focusable.length) { event.preventDefault(); return }
    const first = focusable[0], last = focusable.at(-1)!
    if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog.current)) { event.preventDefault(); last.focus() }
    else if (!event.shiftKey && (document.activeElement === last || document.activeElement === dialog.current)) { event.preventDefault(); first.focus() }
  }

  return createPortal(<div className="case-picker-backdrop" onClick={event => { if (event.target === event.currentTarget) onClose() }}>
    <div className="case-picker" role="dialog" aria-modal="true" aria-labelledby="case-picker-title" tabIndex={-1} ref={dialog} onKeyDown={keydown}>
      <header className="case-picker-heading"><div><span className="eyebrow">PYTEST / CASE SELECTION</span><h2 id="case-picker-title">{suite.name} · 用例选择</h2><p>先看测试内容，再选择本次要执行的范围。</p></div><button onClick={onClose} className="case-picker-close" aria-label="关闭用例选择">×</button></header>
      {error && <div className="alert" role="alert">{error}<button onClick={() => setReload(value => value + 1)}>刷新目录</button></div>}
      {loading ? <div className="case-picker-loading">正在通过 pytest 收集真实用例…</div> : directory && <>
        <div className="case-picker-toolbar"><span>收集到 <strong>{directory.count}</strong> 条 · 已选 <strong>{selected.length}</strong> 条</span><div><button onClick={() => setSelected(directory.cases.map(test => test.id))}>全选</button><button onClick={() => setSelected([])}>清空</button></div></div>
        <div className="case-picker-body">
          <div className="catalog-cases" aria-label="可执行用例">{directory.cases.map(test => <div className={`catalog-case ${inspected === test.id ? 'inspected' : ''}`} key={test.id}>
            <input type="checkbox" aria-label={`选择 ${test.name}`} checked={selected.includes(test.id)} onChange={() => toggle(test.id)} />
            <button className="catalog-case-title" onClick={() => setInspected(test.id)} aria-pressed={inspected === test.id}><strong>{test.title}</strong><code>{test.name}</code><small>{test.source_file}:{test.source_line}</small></button>
            {test.requires_postgres && <span className="suite-tag">PG</span>}
          </div>)}</div>
          <section className="catalog-detail" aria-label="用例说明">{detail && <><span className="eyebrow">CASE DETAILS</span><h3>{detail.title}</h3><code className="catalog-node-id">{detail.id}</code><h4>测试做什么</h4><p>{detail.description}</p><h4>预期结果</h4><p>{detail.expected}</p>{Object.keys(detail.parameters).length > 0 && <><h4>本条参数</h4><dl>{Object.entries(detail.parameters).map(([name, value]) => <div key={name}><dt>{name}</dt><dd><code>{value}</code></dd></div>)}</dl></>}<h4>验证范围</h4><p>{detail.requires_postgres ? '接口与真实 PostgreSQL 数据验证，使用独立测试库。' : '无需订单数据库的契约／执行行为验证。'}</p><p className="catalog-authored-note">业务说明由开发者编写，实际结果由执行后的 pytest 断言决定。</p></>}</section>
        </div>
      </>}
      <footer className="case-picker-footer"><span>{directory ? `目录读取于 ${new Date(directory.collected_at).toLocaleTimeString('zh-CN', { hour12: false })}` : '目录读取失败时不能应用选择'} · 不修改测试代码</span><div><button onClick={onClose}>返回</button><button className="case-picker-apply" disabled={loading || !directory || selected.length === 0} onClick={() => { if (directory) onApply({ ids: selected.length === directory.count ? null : directory.cases.filter(test => selected.includes(test.id)).map(test => test.id), revision: directory.catalog_revision, count: directory.count }) }}>应用选择{selected.length > 0 ? ` · ${selected.length} 条` : ''}</button></div></footer>
    </div>
  </div>, document.body)
}
