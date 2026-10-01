import { useEffect, useRef } from 'react'
import type { ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { useT } from '../i18n'
import { Icon } from './Icon'

interface DialogProps {
  title: string
  onClose: () => void
  children: ReactNode
}

/** A small centered window (forms, confirmations). Closes with the ✕, Esc
 *  or a click outside. */
export function Dialog({ title, onClose, children }: DialogProps) {
  const { t } = useT()
  const close = useRef(onClose)
  useEffect(() => {
    close.current = onClose
  })

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && close.current()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  return createPortal(
    <div className="chart-modal-backdrop" onClick={onClose}>
      <div
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="panel-head">
          <h2 className="chart-modal-title">{title}</h2>
          <button type="button" className="icon-button" aria-label={t('common.close')} onClick={onClose}>
            <Icon name="close" />
          </button>
        </div>
        {children}
      </div>
    </div>,
    document.body,
  )
}
