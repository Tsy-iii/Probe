import { useEffect, useRef, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

// 数据说明与记录详情沿用执行详情的抽屉外观，打开时不改变表格布局。
export default function DataDrawer({ title, subtitle, children, onClose, eyebrow = 'DATA / CONTEXT', closeLabel = '关闭数据详情', busy = false }: { title: string; subtitle: string; children: ReactNode; onClose: () => void; eyebrow?: string; closeLabel?: string; busy?: boolean }) {
  const panel = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    // 等上一层抽屉恢复焦点后再聚焦；也避免 StrictMode 的效果重放触发空字段失焦校验。
    const focusFrame = requestAnimationFrame(() => {
      const initial = panel.current?.querySelector<HTMLElement>('[data-initial-focus]:not(:disabled)')
      if (initial) initial.focus(); else panel.current?.focus()
    })
    return () => {
      cancelAnimationFrame(focusFrame)
      document.body.style.overflow = overflow
      if (previous?.isConnected) previous.focus()
      else document.querySelector<HTMLElement>('.data-table-tabs button.current')?.focus()
    }
  }, [])
  return createPortal(<div className="run-inspector-backdrop" onClick={event => { if (!busy && event.currentTarget === event.target) onClose() }}>
    <div className="run-inspector data-drawer" role="dialog" aria-modal="true" aria-labelledby="data-drawer-title" ref={panel} tabIndex={-1} onKeyDown={event => {
      if (event.key === 'Escape') { event.stopPropagation(); if (!busy) onClose() }
      if (event.key !== 'Tab' || !panel.current) return
      const items = [...panel.current.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), textarea:not(:disabled), a[href], summary')]
      const first = items[0], last = items.at(-1)
      if (!first || !last) { event.preventDefault(); return }
      if (event.shiftKey && (document.activeElement === first || document.activeElement === panel.current)) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && (document.activeElement === last || document.activeElement === panel.current)) { event.preventDefault(); first.focus() }
    }}>
      <header className="run-inspector-heading"><div><span className="eyebrow">{eyebrow}</span><h2 id="data-drawer-title">{title}</h2><p>{subtitle}</p></div><button aria-label={closeLabel} disabled={busy} onClick={onClose}>×</button></header>
      <div className="run-inspector-body">{children}</div>
    </div>
  </div>, document.body)
}
