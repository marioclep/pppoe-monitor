import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { AuthProvider } from './context/AuthContext'
import { SiteProvider } from './context/SiteContext'
import { ProtectedRoute } from './components/ProtectedRoute'
import { Layout } from './components/Layout'
import { Login } from './pages/Login'
import { Dashboard } from './pages/Dashboard'
import { Clients } from './pages/Clients'
import { ClientDetail } from './pages/ClientDetail'
import { RouterDetail } from './pages/RouterDetail'
import { RoutersAdmin } from './pages/RoutersAdmin'
import { SettingsAdmin } from './pages/SettingsAdmin'
import { Alerts } from './pages/Alerts'
import { Server } from './pages/Server'
import { UsersAdmin } from './pages/UsersAdmin'

export function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<Login />} />
          <Route element={<ProtectedRoute />}>
            <Route
              element={
                <SiteProvider>
                  <Layout />
                </SiteProvider>
              }
            >
              <Route path="/" element={<Navigate to="/dashboard" replace />} />
              <Route path="/dashboard" element={<Dashboard />} />
              <Route path="/clients" element={<Clients />} />
              <Route path="/clients/:id" element={<ClientDetail />} />
              <Route path="/routers" element={<RoutersAdmin />} />
              <Route path="/routers/:id" element={<RouterDetail />} />
              <Route path="/alerts" element={<Alerts />} />
              <Route path="/server" element={<Server />} />
              <Route path="/settings" element={<SettingsAdmin />} />
              <Route path="/users" element={<UsersAdmin />} />
            </Route>
          </Route>
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  )
}
