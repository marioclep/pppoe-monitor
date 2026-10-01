import type { ReactNode } from 'react'
import { ThemeToggle } from './ThemeToggle'

export function PageHeader({ title, children }: { title: ReactNode; children?: ReactNode }) {
  return (
    <div className="page-header">
      <h1>{title}</h1>
      <div className="header-actions">
        {children}
        <ThemeToggle />
      </div>
    </div>
  )
}
