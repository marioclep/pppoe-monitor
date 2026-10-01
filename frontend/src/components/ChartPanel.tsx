import { useState } from 'react'
import type { ReactNode } from 'react'
import { useT } from '../i18n'
import { ChartModal } from './ChartModal'
import { Icon } from './Icon'
import { Panel } from './Panel'

interface ChartPanelProps {
  title: ReactNode
  action?: ReactNode
  /** Draws the chart; `expanded` is true inside the modal, where it should
   *  use height="100%" to fill it. */
  children: (expanded: boolean) => ReactNode
}

/** A Panel holding a chart, with a button that shows it full screen. */
export function ChartPanel({ title, action, children }: ChartPanelProps) {
  const [expanded, setExpanded] = useState(false)
  return (
    <>
      <Panel
        title={title}
        action={
          <div className="panel-actions">
            {action}
            <ExpandButton onClick={() => setExpanded(true)} />
          </div>
        }
      >
        {children(false)}
      </Panel>
      {expanded && (
        <ChartModal title={title} action={action} onClose={() => setExpanded(false)}>
          {children(true)}
        </ChartModal>
      )}
    </>
  )
}

export function ExpandButton({ onClick }: { onClick: () => void }) {
  const { t } = useT()
  return (
    <button
      type="button"
      className="icon-button expand-button"
      aria-label={t('chart.expandLabel')}
      title={t('chart.expand')}
      onClick={onClick}
    >
      <Icon name="expand" />
    </button>
  )
}
