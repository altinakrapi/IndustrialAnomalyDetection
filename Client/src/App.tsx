import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import {
  Activity,
  AlertTriangle,
  BarChart3,
  Bell,
  Cpu,
  Database,
  FileChartColumn,
  FlaskConical,
  Gauge,
  LineChart as LineChartIcon,
  LogOut,
  ClipboardList,
  Search,
  ScrollText,
  ShieldCheck,
  SlidersHorizontal,
  UserRound,
  Wrench,
} from 'lucide-react'
import { ApiError, getToken, request, requestAllPages, setToken } from './api'
import { AnalysisTools, AuditPage, DeviceDetailPanel, ExplorationPage, MonitoringPage, NotificationsPage, ProfilePage, ReportsPage, SettingsPage } from './pages/WorkflowPages'

import type { User, Device, Dataset, ModelConfig, Analysis, Maintenance, DashboardData, Page, Notice, Theme, NotificationItem } from './types'

import { Overview } from './pages/OverviewPage'
import { DevicesPage } from './pages/DevicesPage'
import { DatasetsPage } from './pages/DatasetsPage'
import { AnalysisPage } from './pages/AnalysisPage'
import { AnomaliesPage } from './pages/AnomaliesPage'
import { MaintenancePage } from './pages/MaintenancePage'
import { UsersPage } from './pages/UsersPage'
import { MeasurementsPage } from './pages/MeasurementsPage'
import { EvaluationPage } from './pages/EvaluationPage'

import { BrandLogo } from './components/BrandLogo'
import { NoticeBanner } from './components/NoticeBanner'
import { ModelDisclaimer } from './components/ModelDisclaimer'
import { startVisiblePolling } from './polling'

const WELCOME_BACK_LABEL = 'Welcome back'

import { roleLabel } from './constants'

const navigation: { name: Page; icon: typeof Gauge }[] = [
  { name: 'Overview', icon: Gauge },
  { name: 'Monitoring', icon: Activity },
  { name: 'Devices', icon: Cpu },
  { name: 'Measurements', icon: ClipboardList },
  { name: 'Datasets', icon: Database },
  { name: 'Exploration', icon: LineChartIcon },
  { name: 'Analysis', icon: BarChart3 },
  { name: 'Evaluation', icon: FlaskConical },
  { name: 'Anomalies', icon: AlertTriangle },
  { name: 'Maintenance', icon: Wrench },
  { name: 'Reports', icon: FileChartColumn },
  { name: 'Notifications', icon: Bell },
  { name: 'Profile', icon: UserRound },
  { name: 'Users', icon: ShieldCheck },
  { name: 'Audit', icon: ScrollText },
  { name: 'Settings', icon: SlidersHorizontal },
]

function App() {
  const resetTokenFromUrl = new URLSearchParams(window.location.search).get('reset_token')
  const [user, setUser] = useState<User | null>(null)
  const [, setThemeRevision] = useState(0)
  const [authMode, setAuthMode] = useState<'login' | 'register' | 'forgot' | 'reset'>(
    () => resetTokenFromUrl ? 'reset' : 'login',
  )
  const [signupEnabled, setSignupEnabled] = useState(false)
  const [page, setPage] = useState<Page>('Overview')
  const [deviceFocusId, setDeviceFocusId] = useState<number | undefined>()
  const [deviceListScrollY, setDeviceListScrollY] = useState(0)
  const [deviceInitialTab, setDeviceInitialTab] = useState<'Overview' | 'Parameters' | 'Measurements' | 'Analyses' | 'Maintenance' | 'Datasets'>('Overview')
  const [notice, setNotice] = useState<Notice | null>(null)
  function navigateToPage(nextPage: Page) {
    if (nextPage !== page) setNotice(null)
    setPage(nextPage)
  }
  const [loading, setLoading] = useState(false)
  const [search, setSearch] = useState('')
  const [dashboard, setDashboard] = useState<DashboardData | null>(null)
  const [devices, setDevices] = useState<Device[]>([])
  const [datasets, setDatasets] = useState<Dataset[]>([])
  const [models, setModels] = useState<ModelConfig[]>([])
  const [analyses, setAnalyses] = useState<Analysis[]>([])
  const [orders, setOrders] = useState<Maintenance[]>([])
  const [users, setUsers] = useState<User[]>([])
  const [unreviewedEquipmentAnomalies, setUnreviewedEquipmentAnomalies] = useState(0)
  const [unreadNotifications, setUnreadNotifications] = useState(0)
  const [notifications, setNotifications] = useState<NotificationItem[]>([])
  const [notificationError, setNotificationError] = useState('')
  const [notificationsOpen, setNotificationsOpen] = useState(false)
  const [signOutConfirmationOpen, setSignOutConfirmationOpen] = useState(false)
  const notificationsRefreshInFlight = useRef(false)
  const anomalyCountRefreshInFlight = useRef(false)
  const savedTheme = user ? localStorage.getItem(`industrial-anomaly-theme:${user.id}`) : null
  const theme: Theme = savedTheme === 'dark' ? 'dark' : 'light'

  useEffect(() => {
    document.documentElement.classList.toggle('dark', Boolean(user && theme === 'dark'))
  }, [theme, user])

  function changeTheme(nextTheme: Theme) {
    if (!user) return
    localStorage.setItem(`industrial-anomaly-theme:${user.id}`, nextTheme)
    setThemeRevision((revision) => revision + 1)
  }

  const refreshNotifications = useCallback(async (signal?: AbortSignal) => {
    if (notificationsRefreshInFlight.current) return
    notificationsRefreshInFlight.current = true
    if (!user) {
      setUnreadNotifications(0)
      setNotifications([])
      setNotificationError('')
      notificationsRefreshInFlight.current = false
      return
    }
    try {
      const [count, items] = await Promise.all([
        request<{ unread_count: number }>('/notifications/unread-count', { signal }),
        request<NotificationItem[]>('/notifications?limit=5', { signal }),
      ])
      setUnreadNotifications(count.unread_count)
      setNotifications(items)
      setNotificationError('')
    } catch (error) {
      if (!signal?.aborted) {
        setNotificationError(error instanceof Error ? error.message : 'Unable to load notifications')
      }
    } finally {
      notificationsRefreshInFlight.current = false
    }
  }, [user])

  const refreshEquipmentAnomalyCount = useCallback(async (signal?: AbortSignal) => {
    if (anomalyCountRefreshInFlight.current) return
    anomalyCountRefreshInFlight.current = true
    if (!user) {
      setUnreviewedEquipmentAnomalies(0)
      anomalyCountRefreshInFlight.current = false
      return
    }
    try {
      const summary = await request<{ pending_reviews: number }>(
        '/anomalies/summary?source_type=equipment',
        { signal },
      )
      setUnreviewedEquipmentAnomalies(summary.pending_reviews)
    } catch (error) {
      if (!signal?.aborted) {
        setNotice({
          kind: 'error',
          text: error instanceof Error ? error.message : 'Unable to refresh equipment anomaly count',
        })
      }
    } finally {
      anomalyCountRefreshInFlight.current = false
    }
  }, [user])

  const loadPage = useCallback(async () => {
    if (!user) return
    setLoading(true)
    try {
      setDevices(await requestAllPages<Device>('/devices'))
      if (user.role !== 'maintenance_technician') {
        setDatasets(await requestAllPages<Dataset>('/datasets'))
      } else {
        setDatasets([])
      }
      if (page === 'Overview') setDashboard(await request<DashboardData>('/dashboard'))
      if (page === 'Analysis') {
        const [modelData, analysisData] = await Promise.all([
          request<ModelConfig[]>('/analyses/models'),
          request<{ items: Analysis[] }>('/analyses/history?limit=50&offset=0'),
        ])
        setModels(modelData)
        setAnalyses(analysisData.items)
      }
      if (page === 'Maintenance') setOrders(await request<Maintenance[]>('/maintenance'))
      if (page === 'Users' && user.role === 'administrator') setUsers(await request<User[]>('/users'))
    } catch (error) {
      setNotice({ kind: 'error', text: error instanceof Error ? error.message : 'Unable to load data' })
    } finally {
      setLoading(false)
    }
  }, [page, user])

  useEffect(() => {
    if (!user) {
      const frame = window.requestAnimationFrame(() => void refreshNotifications())
      return () => window.cancelAnimationFrame(frame)
    }
    return startVisiblePolling(refreshNotifications, 15000)
  }, [refreshNotifications, user])

  useEffect(() => {
    if (!user) {
      const frame = window.requestAnimationFrame(() => void refreshEquipmentAnomalyCount())
      return () => window.cancelAnimationFrame(frame)
    }
    return startVisiblePolling(refreshEquipmentAnomalyCount, 15000)
  }, [refreshEquipmentAnomalyCount, user])

  useEffect(() => {
    if (resetTokenFromUrl) {
      setToken(null)
      return
    }
    if (!getToken()) return
    let active = true
    const restoreSession = async () => {
      for (let attempt = 1; attempt <= 3; attempt += 1) {
        try {
          const restoredUser = await request<User>('/auth/me')
          if (active) {
            setUser(restoredUser)
            setNotice(null)
          }
          return
        } catch (error) {
          if (!active) return
          if (error instanceof ApiError && error.status === 401) {
            setToken(null)
            setUser(null)
            setNotice({ kind: 'error', text: 'Your session expired. Please sign in again.' })
            return
          }
          const retryable = !(error instanceof ApiError) || error.status >= 500
          if (!retryable) {
            setNotice({
              kind: 'error',
              text: error instanceof Error ? error.message : 'Unable to restore the saved session.',
            })
            return
          }
          if (attempt < 3) {
            setNotice({ kind: 'error', text: 'Server unavailable, retrying session check...' })
            await new Promise((resolve) => window.setTimeout(resolve, 1500))
          } else {
            setNotice({
              kind: 'error',
              text: 'Server unavailable after 3 attempts. Your saved session was kept; refresh to retry.',
            })
          }
        }
      }
    }
    void restoreSession()
    return () => {
      active = false
    }
  }, [resetTokenFromUrl])

  useEffect(() => {
    request<{ signup_enabled: boolean }>('/auth/status')
      .then(({ signup_enabled }) => setSignupEnabled(signup_enabled))
      .catch((error: unknown) => {
        setNotice({
          kind: 'error',
          text: error instanceof Error ? error.message : 'Unable to check account setup status',
        })
      })
  }, [])

  useEffect(() => {
    const handleSessionExpired = () => {
      setToken(null)
      setUser(null)
      setNotice({ kind: 'error', text: 'Your session expired. Please sign in again.' })
      setAuthMode('login')
    }
    window.addEventListener('session-expired', handleSessionExpired)
    return () => window.removeEventListener('session-expired', handleSessionExpired)
  }, [])

  useEffect(() => {
    if (!signOutConfirmationOpen) return
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setSignOutConfirmationOpen(false)
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [signOutConfirmationOpen])

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => void loadPage())
    return () => window.cancelAnimationFrame(frame)
  }, [loadPage])

  useEffect(() => {
    if (page !== 'Analysis' || !analyses.some((analysis) => ['pending', 'running'].includes(analysis.status))) return
    const timer = window.setInterval(() => {
      request<{ items: Analysis[] }>('/analyses/history?limit=50&offset=0').then(({ items }) => setAnalyses(items)).catch((error: unknown) => {
        setNotice({ kind: 'error', text: error instanceof Error ? error.message : 'Unable to refresh analyses' })
      })
    }, 4000)
    return () => window.clearInterval(timer)
  }, [analyses, page])

  async function authenticate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    const isRegistration = authMode === 'register'
    if (isRegistration && form.get('password') !== form.get('password_confirmation')) {
      setNotice({ kind: 'error', text: 'Password and confirm password must match.' })
      return
    }
    const endpoint = isRegistration ? '/auth/register' : '/auth/login'
    try {
      const result = await request<{ access_token: string; user: User }>(endpoint, {
        method: 'POST',
        body: JSON.stringify({
          email: form.get('email'),
          password: form.get('password'),
          ...(isRegistration ? {
            first_name: form.get('first_name'),
            surname: form.get('surname'),
            role: form.get('role'),
          } : {}),
        }),
      })
      setToken(result.access_token)
      setUser(result.user)
      navigateToPage('Overview')
      setNotice({
        kind: 'success',
        text: isRegistration
          ? `${roleLabel[result.user.role] ?? result.user.role} account created`
          : WELCOME_BACK_LABEL,
      })
    } catch (error) {
      setNotice({ kind: 'error', text: error instanceof Error ? error.message : 'Authentication failed' })
    }
  }

  async function requestPasswordReset(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    try {
      const result = await request<{ message: string }>('/auth/forgot-password', {
        method: 'POST',
        body: JSON.stringify({ email: form.get('email') }),
      })
      setNotice({ kind: 'success', text: result.message })
    } catch (error) {
      setNotice({ kind: 'error', text: error instanceof Error ? error.message : 'Unable to request a password reset' })
    }
  }

  async function resetPassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    if (form.get('new_password') !== form.get('confirm_password')) {
      setNotice({ kind: 'error', text: 'The new passwords do not match.' })
      return
    }
    if (!resetTokenFromUrl) {
      setNotice({ kind: 'error', text: 'The password reset link is invalid or expired.' })
      return
    }
    try {
      const result = await request<{ message: string }>('/auth/reset-password', {
        method: 'POST',
        body: JSON.stringify({
          token: resetTokenFromUrl,
          new_password: form.get('new_password'),
        }),
      })
      const url = new URL(window.location.href)
      url.searchParams.delete('reset_token')
      window.history.replaceState({}, '', `${url.pathname}${url.search}${url.hash}`)
      setAuthMode('login')
      setNotice({ kind: 'success', text: result.message })
    } catch (error) {
      setNotice({ kind: 'error', text: error instanceof Error ? error.message : 'Unable to reset the password' })
    }
  }

  function logout() {
    setToken(null)
    setUser(null)
    navigateToPage('Overview')
    setNotificationsOpen(false)
    setSignOutConfirmationOpen(false)
  }

  async function openNotification(notification: NotificationItem) {
    setNotificationsOpen(false)
    if (!notification.is_read) {
      try {
        await request(`/notifications/${notification.id}/read`, { method: 'PATCH' })
        await refreshNotifications()
      } catch (error) {
        const message = error instanceof Error ? error.message : 'Unable to update notification'
        setNotificationError(message)
        setNotice({ kind: 'error', text: message })
      }
    }
    navigateToPage('Notifications')
  }

  async function runAction(action: () => Promise<unknown>, success: string) {
    try {
      await action()
      setNotice({ kind: 'success', text: success })
      await loadPage()
      await Promise.all([refreshNotifications(), refreshEquipmentAnomalyCount()])
    } catch (error) {
      setNotice({ kind: 'error', text: error instanceof Error ? error.message : 'The action failed' })
    }
  }

  if (!user) {
    return (
      <div className="login-shell grid min-h-screen bg-white lg:grid-cols-[1fr_0.82fr]">
        <div className="login-hero relative flex min-h-[360px] flex-col justify-between overflow-hidden bg-[#1d3025] p-8 text-white sm:p-12 lg:min-h-screen lg:p-16">
          <BrandLogo light className="relative z-10" />
          <div className="relative z-10 my-12 max-w-lg">
            <span className="text-[10px] font-bold tracking-[0.17em] text-forest-600">INDUSTRIAL INTELLIGENCE</span>
            <h1>See the signal<br />before the failure.</h1>
            <p>Monitor equipment health, find unusual sensor behavior, and turn insights into action.</p>
          </div>
          <div className="relative z-10 flex gap-6 text-[10px] font-semibold tracking-[0.18em] text-slate-400"><span>01 / MONITOR</span><span>02 / ANALYZE</span><span>03 / ACT</span></div>
          <div className="pointer-events-none absolute rounded-full border border-forest-300/10 right-[-210px] top-[-170px] size-[560px]" /><div className="pointer-events-none absolute rounded-full border border-forest-300/10 right-[-100px] top-[-60px] size-[350px]" />
        </div>
        <main className="flex min-h-[620px] flex-col items-center justify-center px-6 py-12 lg:min-h-screen">
          <div className="login-card w-full max-w-md [&_label]:grid [&_label]:gap-1.5 [&_label]:text-xs [&_label]:font-medium [&_label]:text-slate-700 [&_input]:h-11 [&_input]:w-full [&_input]:rounded-lg [&_input]:border [&_input]:border-slate-200 [&_input]:bg-white [&_input]:px-3 [&_input]:text-sm [&_input]:outline-none [&_input]:transition [&_input]:focus:border-forest-500 [&_input]:focus:ring-2 [&_input]:focus:ring-forest-100">
            <BrandLogo className="mb-12" />
            <span className="text-[10px] font-bold tracking-[0.17em] text-forest-600">
              {authMode === 'register' ? 'CREATE STAFF ACCOUNT' : authMode === 'forgot' ? 'ACCOUNT RECOVERY' : authMode === 'reset' ? 'CHOOSE A NEW PASSWORD' : 'SECURE WORKSPACE'}
            </span>
            <h2>{authMode === 'register' ? 'Create your account' : authMode === 'forgot' ? 'Forgot your password?' : authMode === 'reset' ? 'Reset your password' : WELCOME_BACK_LABEL}</h2>
            <p className="text-sm leading-6 text-slate-500">
              {authMode === 'register'
                ? 'Choose your role to create an account and open its dashboard.'
                : authMode === 'forgot'
                  ? 'Enter the email address associated with your account and we will send you a reset link.'
                  : authMode === 'reset'
                    ? 'Choose a new password for your account.'
                    : 'Sign in to your equipment intelligence platform.'}
            </p>
            {authMode === 'forgot' && <form className="mt-8 grid gap-5" onSubmit={requestPasswordReset}>
              <label>Email address<input name="email" type="email" required autoComplete="email" placeholder="you@company.com" /></label>
              {notice && <NoticeBanner notice={notice} />}
              <button className="inline-flex min-h-10 cursor-pointer items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold bg-forest-600 text-white shadow-sm hover:bg-forest-700 w-full justify-between" type="submit">Send reset link<span aria-hidden="true">{'->'}</span></button>
              <button className="text-center text-sm font-medium text-forest-700 hover:text-forest-900" type="button" onClick={() => { setAuthMode('login'); setNotice(null) }}>Back to sign in</button>
            </form>}
            {authMode === 'reset' && <form className="mt-8 grid gap-5" onSubmit={resetPassword}>
              <label>New password (at least 10 characters)<input name="new_password" type="password" required minLength={10} maxLength={128} autoComplete="new-password" /></label>
              <label>Confirm new password<input name="confirm_password" type="password" required minLength={10} maxLength={128} autoComplete="new-password" /></label>
              {notice && <NoticeBanner notice={notice} />}
              <button className="inline-flex min-h-10 cursor-pointer items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold bg-forest-600 text-white shadow-sm hover:bg-forest-700 w-full justify-between" type="submit">Update password<span aria-hidden="true">{'->'}</span></button>
              <button className="text-center text-sm font-medium text-forest-700 hover:text-forest-900" type="button" onClick={() => { setAuthMode('login'); setNotice(null) }}>Back to sign in</button>
            </form>}
            {(authMode === 'login' || authMode === 'register') && <>
              <form className="mt-8 grid gap-5" onSubmit={authenticate}>
                {authMode === 'register' && <div className="grid gap-5 sm:grid-cols-2">
                  <label>First name<input name="first_name" required autoComplete="given-name" placeholder="First name" /></label>
                  <label>Surname<input name="surname" required autoComplete="family-name" placeholder="Surname" /></label>
                </div>}
                <label>Email address<input name="email" type="email" required autoComplete="email" placeholder="you@company.com" /></label>
                {authMode === 'register' && <label>Role<select name="role" required defaultValue="data_analyst"><option value="data_analyst">Data Analyst</option><option value="maintenance_technician">Maintenance Technician</option></select></label>}
                <label>Password<input name="password" type="password" required minLength={10} autoComplete={authMode === 'register' ? 'new-password' : 'current-password'} placeholder={authMode === 'register' ? 'At least 10 characters' : 'Your password'} /></label>
                {authMode === 'register' && <label>Confirm password<input name="password_confirmation" type="password" required minLength={10} autoComplete="new-password" placeholder="Enter your password again" /></label>}
                {notice && <NoticeBanner notice={notice} />}
                <button className="inline-flex min-h-10 cursor-pointer items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-semibold transition-colors disabled:cursor-not-allowed disabled:opacity-50 bg-forest-600 text-white shadow-sm hover:bg-forest-700 w-full justify-between" type="submit">{authMode === 'register' ? 'Create account' : 'Sign in'}<span aria-hidden="true">{'->'}</span></button>
              </form>
              {authMode === 'login' && <button className="mt-4 w-full text-center text-sm font-medium text-forest-700 hover:text-forest-900" onClick={() => { setAuthMode('forgot'); setNotice(null) }}>Forgot password?</button>}
              {signupEnabled
                ? <button className="mt-6 w-full text-center text-sm font-medium text-forest-700 hover:text-forest-900" onClick={() => {
                    setAuthMode(authMode === 'login' ? 'register' : 'login')
                    setNotice(null)
                  }}>
                    {authMode === 'login' ? 'Need an account? Sign up' : 'Already have an account? Sign in'}
                  </button>
                : authMode === 'login' && <p className="mt-6 text-center text-xs leading-5 text-slate-500">New account signup will be available after an Administrator account has been seeded.</p>}
            </>}
          </div>
          <span className="mt-auto pt-12 text-xs text-slate-400 lg:absolute lg:bottom-8">Your system data stays in your own workspace.</span>
        </main>
      </div>
    )
  }

  const canAnalyze = user.role === 'administrator' || user.role === 'data_analyst'
  const visibleNavigation = navigation.filter((item) => {
    if (user.role === 'administrator') return true
    if (user.role === 'data_analyst') {
      return !['Users', 'Audit', 'Settings'].includes(item.name)
    }
    return ['Overview', 'Devices', 'Measurements', 'Evaluation', 'Anomalies', 'Maintenance', 'Reports', 'Notifications', 'Profile'].includes(item.name)
  })
  const displayedDevices = devices.filter((device) => (
    (user.role !== 'maintenance_technician' || device.responsible_user_id === user.id)
    && `${device.name} ${device.device_code} ${device.location ?? ''}`.toLowerCase().includes(search.toLowerCase())
  ))

  return (
    <div className="app-shell min-h-screen bg-[#f4f6ef] text-[#182334] lg:grid lg:grid-cols-[260px_minmax(0,1fr)]">
    <aside className="app-sidebar sticky top-0 z-20 flex h-auto flex-col border-b border-slate-200 px-4 py-4 shadow-sm lg:h-screen lg:border-b-0 lg:border-r lg:px-5 lg:py-6">
      <BrandLogo className="mb-8 min-w-0" />
      <nav className="flex gap-1 overflow-x-auto lg:mt-3 lg:flex-col lg:gap-1.5 lg:overflow-y-auto" aria-label="Main navigation">
          {visibleNavigation.map(({ name, icon: Icon }) => (
            <button key={name} className={`flex shrink-0 items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition ${page === name ? 'bg-forest-50 text-forest-700 hover:bg-forest-50 hover:text-forest-700' : 'text-slate-500 hover:bg-slate-50 hover:text-slate-900'}`} onClick={() => { navigateToPage(name); setSearch(''); if (name === 'Devices') { setDeviceFocusId(undefined); setDeviceInitialTab('Overview') } }}>
              <Icon size={18} strokeWidth={1.8} /><span>{name}</span>
              {name === 'Anomalies' && unreviewedEquipmentAnomalies > 0 ? <span className="ml-auto rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-bold text-amber-700" aria-label={`${unreviewedEquipmentAnomalies} unreviewed equipment anomalies`} title="Unreviewed registered-equipment anomalies">{unreviewedEquipmentAnomalies}</span> : null}
              {name === 'Notifications' && unreadNotifications > 0 ? <span className="ml-auto rounded-full bg-forest-100 px-2 py-0.5 text-[10px] font-bold text-forest-700">{unreadNotifications}</span> : null}
            </button>
          ))}
        </nav>
        <div className="mt-auto hidden lg:block">
          <div className="flex items-center gap-3 rounded-xl border-t border-slate-100 px-2 pt-4">
            <div className="grid size-9 shrink-0 place-items-center rounded-full bg-forest-100 text-xs font-bold text-forest-700">{user.full_name.split(' ').map((part) => part[0]).join('').slice(0, 2).toUpperCase()}</div>
            <div className="grid min-w-0 flex-1 [&_strong]:truncate [&_strong]:text-xs [&_small]:text-[11px] [&_small]:text-slate-500"><strong>{user.full_name}</strong><small>{roleLabel[user.role] ?? user.role}</small></div>
            <button className="grid size-9 place-items-center rounded-lg text-slate-500 hover:bg-slate-100 hover:text-slate-900 logout-button" aria-label="Sign out" title="Sign out" onClick={() => setSignOutConfirmationOpen(true)}><LogOut size={17} /></button>
          </div>
        </div>
      </aside>

      <main className="flex min-h-screen min-w-0 flex-col">
        <header className="app-topbar sticky top-0 z-10 flex min-h-16 items-center justify-between gap-4 border-b border-slate-200 px-5 sm:px-8">
          <div className="monitoring-breadcrumb flex items-center gap-2 text-xs text-slate-600 [&>strong]:font-semibold [&>strong]:text-slate-800"><span>Workspace</span><span>/</span><strong>{page}</strong>{page === 'Devices' && deviceFocusId !== undefined && <><span>/</span><strong>{devices.find((device) => device.id === deviceFocusId)?.name ?? 'Device'}</strong></>}</div>
          <div className="flex items-center gap-3">
            <span className="hidden text-xs text-slate-500 sm:block">{new Date().toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' })}</span>
            {['Devices', 'Measurements', 'Datasets', 'Analysis', 'Anomalies', 'Maintenance'].includes(page) && <label className="flex h-9 items-center gap-2 rounded-lg border border-slate-200 bg-white px-3 text-slate-400 [&>input]:w-32 [&>input]:border-0 [&>input]:bg-transparent [&>input]:text-xs [&>input]:text-slate-700 [&>input]:outline-none sm:[&>input]:w-44"><Search size={16} /><input aria-label={`Search ${page.toLowerCase()}`} placeholder={`Search ${page.toLowerCase()}...`} value={search} onChange={(event) => setSearch(event.target.value)} /></label>}
            <div className="relative">
              <button
                className="relative grid size-9 place-items-center rounded-lg text-slate-500 hover:bg-slate-100 hover:text-slate-900"
                aria-label={`Notifications${unreadNotifications ? `, ${unreadNotifications} unread` : ''}`}
                aria-expanded={notificationsOpen}
                aria-haspopup="true"
                onClick={() => {
                  const nextOpen = !notificationsOpen
                  setNotificationsOpen(nextOpen)
                  if (nextOpen) void refreshNotifications()
                }}
              >
                <Bell size={18} />
                {unreadNotifications > 0 && <span className="absolute -right-1 -top-1 grid min-h-4 min-w-4 place-items-center rounded-full bg-rose-600 px-1 text-[9px] font-bold leading-none text-white">{unreadNotifications > 99 ? '99+' : unreadNotifications}</span>}
              </button>
              {notificationsOpen && <div className="notification-popover absolute right-0 z-30 mt-2 w-[min(22rem,calc(100vw-2rem))] overflow-hidden rounded-xl border border-slate-200 bg-white shadow-xl" role="dialog" aria-label="Recent notifications">
                <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
                  <strong className="text-sm text-slate-900">Notifications</strong>
                  <span className="text-[10px] font-semibold text-slate-500">{unreadNotifications} unread</span>
                </div>
                {notificationError && <p className="px-4 py-3 text-xs text-rose-700" role="alert">{notificationError}</p>}
                <div className="max-h-80 overflow-y-auto">
                  {notifications.map((item) => <button key={item.id} className={`block w-full border-b border-slate-100 px-4 py-3 text-left hover:bg-slate-50 ${item.is_read ? '' : 'bg-forest-50/50'}`} onClick={() => void openNotification(item)}>
                    <span className="flex items-start gap-2"><span className={`mt-1.5 size-2 shrink-0 rounded-full ${item.is_read ? 'bg-slate-300' : 'bg-forest-600'}`} /><span className="min-w-0"><strong className="block truncate text-xs text-slate-900">{item.title}</strong><span className="mt-1 line-clamp-2 block text-[11px] leading-4 text-slate-600">{item.message}</span><small className="mt-1 block text-[10px] text-slate-400">{new Date(item.created_at).toLocaleString()}</small></span></span>
                  </button>)}
                  {notifications.length === 0 && !notificationError && <p className="px-4 py-6 text-center text-xs text-slate-500">You’re all caught up.</p>}
                </div>
                <button className="w-full px-4 py-3 text-center text-xs font-semibold text-forest-700 hover:bg-forest-50" onClick={() => { setNotificationsOpen(false); navigateToPage('Notifications') }}>View all notifications</button>
              </div>}
            </div>
            <button className="grid size-9 place-items-center rounded-lg text-slate-500 hover:bg-slate-100 hover:text-slate-900 logout-button" aria-label="Sign out" title="Sign out" onClick={() => setSignOutConfirmationOpen(true)}><LogOut size={18} /></button>
          </div>
        </header>
        <section className="page-content mx-auto w-full max-w-[1600px] flex-1 space-y-6 px-5 py-7 sm:px-8 sm:py-9 [&_label]:grid [&_label]:gap-1.5 [&_label]:text-xs [&_label]:font-medium [&_label]:text-slate-600 [&_input]:h-10 [&_input]:min-w-0 [&_input]:rounded-lg [&_input]:border [&_input]:border-slate-200 [&_input]:bg-white [&_input]:px-3 [&_input]:text-xs [&_input]:text-slate-800 [&_input]:outline-none [&_input]:transition [&_input]:focus:border-forest-500 [&_input]:focus:ring-2 [&_input]:focus:ring-forest-100 [&_select]:h-10 [&_select]:min-w-0 [&_select]:rounded-lg [&_select]:border [&_select]:border-slate-200 [&_select]:bg-white [&_select]:px-3 [&_select]:text-xs [&_select]:text-slate-800 [&_select]:outline-none [&_select]:focus:border-forest-500 [&_select]:focus:ring-2 [&_select]:focus:ring-forest-100 [&_textarea]:rounded-lg [&_textarea]:border [&_textarea]:border-slate-200 [&_textarea]:px-3 [&_textarea]:py-2 [&_textarea]:text-xs">
          {notice && <div className={page === 'Devices' && deviceFocusId !== undefined ? 'fixed right-4 top-20 z-50 w-[min(36rem,calc(100vw-2rem))]' : ''}><NoticeBanner notice={notice} onDismiss={() => setNotice(null)} /></div>}
          {loading && <div className="flex items-center gap-2 text-xs text-slate-500"><span className="loading-skeleton" aria-hidden="true" /> Syncing workspace data...</div>}
          {page === 'Overview' && <Overview user={user} dashboard={dashboard} onNavigate={navigateToPage} />}
          {page === 'Monitoring' && <MonitoringPage onNavigate={(target, query, deviceId) => {
            setSearch(query ?? '')
            if (target === 'Devices') {
              setDeviceFocusId(deviceId)
              setDeviceInitialTab('Overview')
            }
            if (target === 'Anomalies') setDeviceFocusId(deviceId)
            navigateToPage(target)
          }} />}
          {page === 'Devices' && <>
            <div hidden={deviceFocusId !== undefined}>
              <DevicesPage devices={displayedDevices} canEdit={canAnalyze} isAdministrator={user.role === 'administrator'} onAction={runAction} onSelectDevice={(deviceId) => {
                setDeviceListScrollY(window.scrollY)
                setDeviceFocusId(deviceId)
                setDeviceInitialTab('Overview')
              }} />
            </div>
            {deviceFocusId !== undefined && <DeviceDetailPanel devices={displayedDevices} canEdit={user.role === 'administrator'} canRecord={['administrator', 'data_analyst', 'maintenance_technician'].includes(user.role)} isAdministrator={user.role === 'administrator'} currentUserId={user.id} initialDeviceId={deviceFocusId} initialTab={deviceInitialTab} onDeviceIdChange={setDeviceFocusId} onBackToDevices={() => {
              setDeviceFocusId(undefined)
              window.requestAnimationFrame(() => window.scrollTo(0, deviceListScrollY))
            }} onAction={runAction} />}
          </>}
          {page === 'Measurements' && <MeasurementsPage devices={devices} currentUserId={user.id} userRole={user.role} search={search} onAction={runAction} />}
          {page === 'Datasets' && <DatasetsPage datasets={datasets.filter((item) => `${item.name} ${item.device_name ?? ''} ${item.status}`.toLowerCase().includes(search.toLowerCase()))} canEdit={canAnalyze} onAction={runAction} />}
          {page === 'Exploration' && <ExplorationPage datasets={datasets} onAction={runAction} onNavigate={(target, query) => { setSearch(query ?? ''); navigateToPage(target) }} />}
          {page === 'Analysis' && <><AnalysisTools datasets={datasets} devices={devices} models={models} analyses={analyses} canEdit={canAnalyze} onAction={runAction} /><div className="hidden" aria-hidden="true"><AnalysisPage datasets={[]} models={[]} analyses={[]} canEdit={false} onAction={runAction} /></div></>}
          {page === 'Evaluation' && <EvaluationPage userRole={user.role} onAction={runAction} />}
          {page === 'Anomalies' && <AnomaliesPage
            devices={devices}
            userRole={user.role}
            initialDeviceId={deviceFocusId}
            onAction={runAction}
            onNavigate={(target, query, deviceId) => {
              setSearch(query ?? '')
              if (target === 'Devices') {
                if (deviceId !== undefined) setDeviceFocusId(deviceId)
                setDeviceInitialTab('Analyses')
              } else if (deviceId !== undefined) setDeviceFocusId(deviceId)
              navigateToPage(target)
            }}
          />}
          {page === 'Maintenance' && <MaintenancePage devices={devices} orders={orders.filter((item) => `${item.title} ${item.device_name ?? ''} ${item.status} ${item.priority} ${item.assigned_name ?? ''}`.toLowerCase().includes(search.toLowerCase()))} user={user} onAction={runAction} />}
          {page === 'Reports' && <ReportsPage devices={devices} onAction={runAction} />}
          {page === 'Notifications' && <NotificationsPage onNotificationsChange={refreshNotifications} />}
          {page === 'Profile' && <ProfilePage user={user} onUserUpdate={setUser} onAction={runAction} />}
          {page === 'Users' && user.role === 'administrator' && <UsersPage users={users} currentUserId={user.id} onAction={runAction} />}
          {page === 'Audit' && <AuditPage isAdmin={user.role === 'administrator'} />}
          {page === 'Settings' && <SettingsPage isAdmin={user.role === 'administrator'} theme={theme} onThemeChange={changeTheme} />}
        </section>
        <footer className="flex flex-wrap justify-between gap-2 border-t border-slate-200 px-5 py-4 text-[9px] font-semibold tracking-wider text-slate-400 sm:px-8"><span>INDUSTRIAL ANOMALY DETECTION - INDUSTRIAL EQUIPMENT INTELLIGENCE</span><ModelDisclaimer /></footer>
      </main>
      {signOutConfirmationOpen && <div className="fixed inset-0 z-50 grid place-items-center bg-slate-950/40 p-4" role="presentation" onMouseDown={(event) => {
        if (event.target === event.currentTarget) setSignOutConfirmationOpen(false)
      }}>
        <section className="w-full max-w-sm rounded-2xl bg-white p-6 shadow-2xl" role="alertdialog" aria-modal="true" aria-labelledby="sign-out-title" aria-describedby="sign-out-description">
          <h2 id="sign-out-title" className="text-base font-semibold text-slate-900">Sign out?</h2>
          <p id="sign-out-description" className="mt-2 text-sm leading-6 text-slate-600">Are you sure you want to sign out of your account?</p>
          <div className="mt-6 flex justify-end gap-3">
            <button className="min-h-10 rounded-lg border border-slate-200 px-4 text-sm font-semibold text-slate-700 hover:bg-slate-50" onClick={() => setSignOutConfirmationOpen(false)}>Cancel</button>
            <button className="min-h-10 rounded-lg bg-forest-600 px-4 text-sm font-semibold text-white hover:bg-forest-700" onClick={logout}>Sign out</button>
          </div>
        </section>
      </div>}
    </div>
  )
}

export default App
