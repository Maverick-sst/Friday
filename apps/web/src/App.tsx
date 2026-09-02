import { useEffect, useState } from 'react'
import { Shell, type PageId } from './Shell'
import { ConnectStorePage } from './pages/ConnectStorePage'
import { ProfilePage } from './pages/ProfilePage'
import { PoliciesPage } from './pages/PoliciesPage'
import { AgentConsolePage } from './pages/AgentConsolePage'
import { TracePage } from './pages/TracePage'
import { OnboardingPage } from './pages/OnboardingPage'
import { BaselinePage } from './pages/BaselinePage'
import { TeamPage } from './pages/TeamPage'
import { MissionDetailPage } from './pages/MissionDetailPage'
import { StrategyPage } from './pages/StrategyPage'
import { currentMerchantId, setMerchant } from './lib/team'

export default function App() {
  const [page, setPage] = useState<PageId>('onboarding')
  const [merchantId, setMerchantIdState] = useState<string | null>(null)
  const [missionId, setMissionId] = useState<string | null>(null)
  const [checked, setChecked] = useState(false)

  useEffect(() => {
    const existing = currentMerchantId()
    if (existing) {
      setMerchantIdState(existing)
      setPage('team')
    }
    setChecked(true)
  }, [])

  const handleMerchant = (id: string) => {
    setMerchant(id)
    setMerchantIdState(id)
  }

  const openMission = (id: string) => {
    setMissionId(id)
    setPage('mission')
  }

  const legacyMerchant = merchantId ?? ''

  return (
    <Shell page={page} onNavigate={setPage}>
      {!checked && <p className="mono-data">connecting…</p>}

      {page === 'onboarding' && (
        <OnboardingPage
          onWorkspace={(m, baselineMissionId) => {
            handleMerchant(m)
            setPage(baselineMissionId ? 'baseline' : 'team')
          }}
        />
      )}

      {page === 'baseline' && merchantId && (
        <BaselinePage merchantId={merchantId} onOpenMission={openMission} onDone={() => setPage('team')} />
      )}

      {page === 'team' && merchantId && (
        <TeamPage merchantId={merchantId} onOpenMission={openMission} />
      )}
      {page === 'team' && !merchantId && (
        <p className="text-mute">Onboard a store first to activate your team.</p>
      )}

      {page === 'mission' && missionId && (
        <MissionDetailPage missionId={missionId} onBack={() => setPage('team')} />
      )}

      {page === 'strategy' && merchantId && (
        <StrategyPage merchantId={merchantId} />
      )}
      {page === 'strategy' && !merchantId && (
        <p className="text-mute">Strategy unlocks after your first baseline.</p>
      )}

      {/* ---- Legacy V0 commerce gateway (preserved intact) ---- */}
      {isLegacy(page) && (
        <>
          {page === 'legacy-connect' && (
            <ConnectStorePage
              onConnected={(slug) => {
                handleMerchant(slug)
                setPage('legacy-profile')
              }}
            />
          )}
          {legacyMerchant && page === 'legacy-profile' && <ProfilePage merchantId={legacyMerchant} />}
          {legacyMerchant && page === 'legacy-policies' && <PoliciesPage merchantId={legacyMerchant} />}
          {legacyMerchant && page === 'legacy-console' && (
            <AgentConsolePage key={legacyMerchant} merchantId={legacyMerchant} ready />
          )}
          {legacyMerchant && page === 'legacy-trace' && <TracePage merchantId={legacyMerchant} />}
          {!legacyMerchant && page !== 'legacy-connect' && (
            <p className="text-mute">Connect a store in V0 first.</p>
          )}
        </>
      )}
    </Shell>
  )
}

function isLegacy(p: PageId): boolean {
  return p.startsWith('legacy')
}
