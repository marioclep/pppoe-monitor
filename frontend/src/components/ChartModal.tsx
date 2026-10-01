import { useEffect, useRef } from 'react'
import type { ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useT } from '../i18n'
import { Icon } from './Icon'

interface ChartModalProps {
  title: ReactNode
  action?: ReactNode
  onClose: () => void
  /** Charts drawn with height="100%" fill the modal; several split it evenly. */
  children: ReactNode
}

/** A chart blown up to (almost) the whole screen. Closes with the ✕, Esc or
 *  a click outside. Rendered in <body> so no parent can clip or restyle it. */
export function ChartModal({ title, action, onClose, children }: ChartModalProps) {
  const { t } = useT()
  const close = useRef(onClose)
  useEffect(() => {
    close.current = onClose
  })

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && close.current()
    window.addEventListener('keydown', onKey)
    const overflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      window.removeEventListener('keydown', onKey)
      document.body.style.overflow = overflow
    }
  }, [])

  return createPortal(
    <div className="chart-modal-backdrop" onClick={onClose}>
      <div className="chart-modal" role="dialog" aria-modal="true" onClick={(e) => e.stopPropagation()}>
        <div className="panel-head">
          <h2 className="chart-modal-title">{title}</h2>
          <div className="panel-actions">
            {action}
            <button type="button" className="icon-button" aria-label={t('common.close')} onClick={onClose}>
              <Icon name="close" />
            </button>
          </div>
        </div>
        <div className="chart-modal-body">{children}</div>
      </div>
    </div>,
    document.body,
  )
}
