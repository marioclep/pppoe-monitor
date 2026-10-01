import { ClientsTable } from '../components/ClientsTable'
import { PageHeader } from '../components/PageHeader'
import { useT } from '../i18n'

export function Clients() {
  const { t } = useT()
  return (
    <>
      <PageHeader title={t('nav.clients')} />
      <ClientsTable />
    </>
  )
}
