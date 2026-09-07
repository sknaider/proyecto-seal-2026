import { useCallback, useEffect, useState } from 'react'
import { API } from '../App'
import { CreditCard, Check, Shield } from 'lucide-react'

interface Plan {
  id: string
  name: string
  price_usd_month: number | null
  features: string[]
  limits: Record<string, number>
}

export default function BillingView() {
  const [plans, setPlans] = useState<Plan[]>([])
  const [current, setCurrent] = useState('free')
  const [saving, setSaving] = useState<string | null>(null)

  const load = useCallback(async () => {
    const [plansR, subR] = await Promise.all([
      fetch(`${API}/api/billing/plans`).then(r => r.json()).catch(() => ({})),
      fetch(`${API}/api/billing/subscription`).then(r => r.json()).catch(() => ({})),
    ])
    setPlans(plansR.plans || [])
    if (subR.plan_id) setCurrent(subR.plan_id)
  }, [])

  useEffect(() => { void load() }, [load])

  async function selectPlan(id: string) {
    setSaving(id)
    try {
      await fetch(`${API}/api/billing/subscription`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ plan_id: id, status: 'local' }),
      })
      setCurrent(id)
    } finally {
      setSaving(null)
    }
  }

  return (
    <div className="h-full overflow-y-auto bg-seal-bg p-4">
      <div className="max-w-5xl mx-auto">
        <div className="flex items-center gap-2 mb-4">
          <CreditCard className="w-5 h-5 text-blue-500" />
          <h1 className="text-xl font-semibold text-slate-800">Planes</h1>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          {plans.map(plan => (
            <div key={plan.id} className={`rounded-lg border bg-seal-surface p-4 ${current === plan.id ? 'border-blue-500' : 'border-seal-border'}`}>
              <div className="flex items-center justify-between mb-3">
                <div>
                  <h2 className="text-base font-semibold text-slate-800">{plan.name}</h2>
                  <p className="text-xs text-seal-muted">
                    {plan.price_usd_month === 0 ? '$0/mes' : plan.price_usd_month == null ? 'Precio pendiente' : `$${plan.price_usd_month}/mes`}
                  </p>
                </div>
                {current === plan.id && <Check className="w-5 h-5 text-emerald-600" />}
              </div>

              <div className="space-y-1.5 mb-4">
                {plan.features.map(feature => (
                  <div key={feature} className="flex items-center gap-2 text-xs text-slate-600">
                    <span className="w-1.5 h-1.5 rounded-full bg-blue-500" />
                    <span>{feature.replaceAll('_', ' ')}</span>
                  </div>
                ))}
              </div>

              <div className="rounded border border-seal-border bg-white/60 p-2 mb-4 text-[11px] text-seal-muted">
                {Object.entries(plan.limits).map(([key, value]) => (
                  <div key={key} className="flex justify-between">
                    <span>{key.replaceAll('_', ' ')}</span>
                    <span>{value}</span>
                  </div>
                ))}
              </div>

              <button
                onClick={() => selectPlan(plan.id)}
                disabled={saving === plan.id || current === plan.id}
                className="w-full rounded bg-blue-600 px-3 py-2 text-xs text-white disabled:opacity-50"
              >
                {current === plan.id ? 'Activo localmente' : 'Usar plan'}
              </button>
            </div>
          ))}
        </div>

        <div className="mt-4 flex items-center gap-2 rounded-lg border border-seal-border bg-seal-surface p-3 text-xs text-slate-600">
          <Shield className="w-4 h-4 text-emerald-600 shrink-0" />
          <span>El gating de planes corre localmente. Pagos reales quedan apagados hasta definir precios y proveedor.</span>
        </div>
      </div>
    </div>
  )
}
