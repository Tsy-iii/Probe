import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react'
import { createPortal } from 'react-dom'

interface Option { value: string; label: string }
interface Props {
  ariaLabel: string; value: string; options: Option[]
  onChange: (value: string) => void; disabled?: boolean
}

// 原生 select 的弹层由操作系统绘制。页面菜单统一主题，并保留键盘选择能力。
export default function WorkbenchSelect({ ariaLabel, value, options, onChange, disabled = false }: Props) {
  const menuId = useId()
  const anchor = useRef<HTMLButtonElement>(null)
  const menu = useRef<HTMLDivElement>(null)
  const scrollForKeyboard = useRef(false)
  const [open, setOpen] = useState(false)
  const [activeValue, setActiveValue] = useState<string | null>(null)
  const [position, setPosition] = useState({ left: 0, top: 0, width: 220, maxHeight: 360 })
  const selected = options.find(option => option.value === value)
  // 后台更新选项时保留用户正在浏览的项，不按每次刷新重置键盘位置。
  const activeIndex = Math.max(0, options.findIndex(option => option.value === activeValue))

  function show() {
    if (disabled || options.length === 0 || !anchor.current) return
    const rect = anchor.current.getBoundingClientRect()
    const width = Math.min(Math.max(rect.width, 240), window.innerWidth - 24)
    const below = window.innerHeight - rect.bottom - 12
    const above = rect.top - 12
    const desired = Math.min(options.length * 36 + 10, 360)
    const useBelow = below >= desired || below >= above
    const maxHeight = Math.max(36, Math.min(desired, useBelow ? below : above))
    setPosition({ left: Math.max(12, Math.min(rect.left, window.innerWidth - width - 12)), top: useBelow ? rect.bottom + 6 : Math.max(6, rect.top - maxHeight - 6), width, maxHeight })
    setActiveValue(selected?.value ?? options[0].value)
    scrollForKeyboard.current = true
    setOpen(true)
  }

  function choose(index: number) {
    const option = options[index]
    if (!option) return
    onChange(option.value)
    setOpen(false)
  }

  function keydown(event: KeyboardEvent<HTMLButtonElement>) {
    if (event.key === 'Escape' && open) { event.preventDefault(); event.stopPropagation(); setOpen(false); return }
    if (event.key === 'Tab') { setOpen(false); return }
    if (['ArrowDown', 'ArrowUp', 'Home', 'End', 'Enter', ' '].includes(event.key)) {
      event.preventDefault()
      if (!open) { show(); return }
      if (event.key === 'Enter' || event.key === ' ') { choose(activeIndex); return }
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? options.length - 1 : Math.max(0, Math.min(options.length - 1, activeIndex + (event.key === 'ArrowDown' ? 1 : -1)))
      scrollForKeyboard.current = true
      setActiveValue(options[next]?.value ?? null)
    }
  }

  useEffect(() => {
    if (!open) return
    const close = () => setOpen(false)
    const outside = (event: PointerEvent) => { if (!anchor.current?.contains(event.target as Node) && !menu.current?.contains(event.target as Node)) close() }
    const scroll = (event: Event) => { if (!menu.current?.contains(event.target as Node)) close() }
    document.addEventListener('pointerdown', outside)
    document.addEventListener('scroll', scroll, true)
    window.addEventListener('resize', close)
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('scroll', scroll, true); window.removeEventListener('resize', close) }
  }, [open])

  useEffect(() => { if (disabled) setOpen(false) }, [disabled])
  useEffect(() => {
    if (!open || !scrollForKeyboard.current) return
    const scroller = menu.current?.querySelector<HTMLElement>('.workbench-select-options')
    const option = menu.current?.querySelector<HTMLElement>('[data-active="true"]')
    if (!scroller || !option) return
    const viewport = scroller.getBoundingClientRect(), item = option.getBoundingClientRect()
    if (item.top < viewport.top) scroller.scrollTop -= viewport.top - item.top
    else if (item.bottom > viewport.bottom) scroller.scrollTop += item.bottom - viewport.bottom
  }, [open, activeValue])

  return <div className="workbench-select">
    <button ref={anchor} type="button" role="combobox" aria-label={ariaLabel} aria-haspopup="listbox" aria-expanded={open} aria-controls={menuId}
      aria-activedescendant={open ? `${menuId}-${activeIndex}` : undefined} disabled={disabled || options.length === 0}
      className={`workbench-select-trigger ${open ? 'is-open' : ''}`} onClick={() => open ? setOpen(false) : show()} onKeyDown={keydown}>
      <span title={selected?.label}>{selected?.label ?? '选择…'}</span><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" aria-hidden="true"><path d="m4 6 4 4 4-4" /></svg>
    </button>
    {open && createPortal(<div ref={menu} id={menuId} role="listbox" aria-label={ariaLabel} className="workbench-select-menu" style={position}>
      <div className="workbench-select-options" style={{ maxHeight: Math.max(26, position.maxHeight - 10) }}>
      {options.map((option, index) => <button type="button" role="option" key={option.value} id={`${menuId}-${index}`} aria-selected={option.value === value}
        data-active={index === activeIndex} className={`workbench-select-option ${index === activeIndex ? 'is-active' : ''}`} title={option.label}
        onPointerDown={event => event.preventDefault()} onPointerEnter={() => { scrollForKeyboard.current = false; setActiveValue(option.value) }} onClick={() => choose(index)}>
        <span>{option.label}</span><svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true" style={{ visibility: option.value === value ? 'visible' : 'hidden' }}><path d="m3 8 3 3 7-7" /></svg>
      </button>)}
      </div>
    </div>, document.body)}
  </div>
}
