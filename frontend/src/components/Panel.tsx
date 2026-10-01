import type { ReactNode } from 'react'

interface PanelProps {
  title?: ReactNode
  action?: ReactNode
  className?: string
  children: ReactNode
}

export function Panel({ title, action, className, children }: PanelProps) {
  return (
    <section className={className ? `panel ${className}` : 'panel'}>
      {(title || action) && (
        <div className="panel-head">
          {title ? <h2 className="panel-title">{title}</h2> : <span />}
          {action}
        </div>
      )}
      {children}
    </section>
  )
}
