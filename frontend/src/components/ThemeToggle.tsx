import { useTheme } from '../context/ThemeContext'
import { useT } from '../i18n'
import { Icon } from './Icon'

export function ThemeToggle() {
  const { theme, toggle } = useTheme()
  const { t } = useT()
  const label = theme === 'dark' ? t('theme.toLight') : t('theme.toDark')
  return (
    <button type="button" className="icon-button" onClick={toggle} title={label} aria-label={label}>
      <Icon name={theme === 'dark' ? 'sun' : 'moon'} />
    </button>
  )
}
