import { useT } from '../i18n'

export function Footer() {
  const { t } = useT()
  return (
    <footer className="site-footer">
      {t('footer.developedBy')} MKE Solutions - <a href="mailto:info@mkesolutions.net">info@mkesolutions.net</a>
    </footer>
  )
}
