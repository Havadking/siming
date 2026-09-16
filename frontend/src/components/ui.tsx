import { useEffect, useRef, useState, type ReactNode } from 'react'
import { MoreHorizontal } from 'lucide-react'

export function Button({ variant, size, children, ...rest }: {
  variant?: 'primary' | 'ghost' | 'danger'
  size?: 'sm'
  children: ReactNode
} & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  const cls = ['btn', variant, size].filter(Boolean).join(' ')
  return <button type="button" className={cls} {...rest}>{children}</button>
}

export interface MenuItem {
  label: string
  icon?: ReactNode
  onClick: () => void
  disabled?: boolean
  danger?: boolean
}

/** ⋯ 下拉菜单。点外面 / Esc 关。 */
export function Menu({ items, title }: { items: MenuItem[]; title?: string }) {
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false) }
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => { document.removeEventListener('mousedown', onDoc); document.removeEventListener('keydown', onKey) }
  }, [open])
  return (
    <div className="menu" ref={ref}>
      <button type="button" className="btn ghost sm iconbtn" aria-label={title ?? '更多'} title={title ?? '更多'}
        aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        <MoreHorizontal />
      </button>
      {open && (
        <div className="menu-pop" role="menu">
          {items.map((it) => (
            <button key={it.label} type="button" role="menuitem" disabled={it.disabled}
              className={it.danger ? 'danger' : undefined}
              onClick={() => { setOpen(false); it.onClick() }}>
              {it.icon}{it.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

export function Pill({ tone = 'neutral', children }: { tone?: 'ok' | 'warn' | 'info' | 'bad' | 'neutral' | 'accent'; children: ReactNode }) {
  return <span className={`pill ${tone}`}>{children}</span>
}
