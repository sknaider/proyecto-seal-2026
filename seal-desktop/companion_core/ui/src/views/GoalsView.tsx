import { useState, useEffect, useCallback } from 'react'
import { API } from '../App'
import { Plus, CheckCircle, Archive, Circle, Trash2 } from 'lucide-react'

interface Goal {
  id: number; title: string; description?: string; status: string
  priority: number; due_date?: string; created_at: string
}

type Status = 'active' | 'completed' | 'archived' | 'all'

const STATUS_TABS: { id: Status; label: string }[] = [
  { id: 'active', label: 'Activas' },
  { id: 'completed', label: 'Listas' },
  { id: 'archived', label: 'Guardadas' },
  { id: 'all', label: 'Todas' },
]

function priorityBar(p: number) {
  const pct = (p / 10) * 100
  const color = p >= 8 ? 'bg-red-500' : p >= 5 ? 'bg-yellow-500' : 'bg-slate-600'
  return (
    <div className="flex items-center gap-1.5">
      <div className="w-16 h-1 bg-seal-border rounded-full overflow-hidden">
        <div className={`h-full ${color} rounded-full`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs text-seal-muted">{p}</span>
    </div>
  )
}

export default function GoalsView() {
  const [goals, setGoals] = useState<Goal[]>([])
  const [status, setStatus] = useState<Status>('active')
  const [showAdd, setShowAdd] = useState(false)
  const [form, setForm] = useState({ title: '', description: '', priority: 5, due_date: '' })

  const load = useCallback(async () => {
    const r = await fetch(`${API}/api/goals?status=${status}`)
    const d = await r.json()
    setGoals(d.goals || [])
  }, [status])

  useEffect(() => { load() }, [load])

  async function createGoal() {
    if (!form.title.trim()) return
    await fetch(`${API}/api/goals`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...form, due_date: form.due_date || undefined }),
    })
    setForm({ title: '', description: '', priority: 5, due_date: '' })
    setShowAdd(false)
    load()
  }

  async function updateStatus(id: number, newStatus: string) {
    await fetch(`${API}/api/goals/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status: newStatus }),
    })
    load()
  }

  async function deleteGoal(id: number) {
    await fetch(`${API}/api/goals/${id}`, { method: 'DELETE' })
    setGoals(g => g.filter(x => x.id !== id))
  }

  return (
    <div className="flex flex-col h-full">
      {/* Status tabs */}
      <div className="flex border-b border-seal-border shrink-0">
        {STATUS_TABS.map(t => (
          <button
            key={t.id}
            onClick={() => setStatus(t.id)}
            className={`flex-1 py-2 text-xs transition-colors ${status === t.id ? 'text-blue-400 border-b-2 border-blue-400' : 'text-seal-muted hover:text-slate-300'}`}
          >{t.label}</button>
        ))}
      </div>

      {/* Goals list */}
      <div className="flex-1 overflow-y-auto p-3 space-y-2">
        {goals.length === 0 && (
          <div className="flex flex-col items-center justify-center h-40 text-seal-muted text-sm gap-2">
            <div className="text-3xl">🎯</div>
            <div>No hay metas {status !== 'all' ? STATUS_TABS.find(t => t.id === status)?.label.toLowerCase() : ''}</div>
          </div>
        )}
        {goals.map(g => (
          <div key={g.id} className="bg-seal-surface border border-seal-border rounded-lg p-3 group">
            <div className="flex items-start gap-2">
              <div className="mt-0.5 shrink-0">
                {g.status === 'completed'
                  ? <CheckCircle size={16} className="text-green-400" />
                  : g.status === 'archived'
                  ? <Archive size={16} className="text-seal-muted" />
                  : <Circle size={16} className="text-blue-400" />
                }
              </div>
              <div className="flex-1 min-w-0">
                <p className={`text-sm font-medium ${g.status === 'completed' ? 'line-through text-seal-muted' : 'text-slate-200'}`}>{g.title}</p>
                {g.description && <p className="text-xs text-seal-muted mt-0.5 leading-relaxed">{g.description}</p>}
                <div className="flex items-center gap-3 mt-1.5">
                  {priorityBar(g.priority)}
                  {g.due_date && <span className="text-xs text-amber-400">{g.due_date}</span>}
                </div>
              </div>
              <div className="flex gap-1 opacity-0 group-hover:opacity-100 transition-opacity shrink-0">
                {g.status !== 'completed' && (
                  <button onClick={() => updateStatus(g.id, 'completed')} className="p-1 text-seal-muted hover:text-green-400 text-xs" title="Marcar lista">✓</button>
                )}
                {g.status === 'active' && (
                  <button onClick={() => updateStatus(g.id, 'archived')} className="p-1 text-seal-muted hover:text-amber-400" title="Guardar">
                    <Archive size={12} />
                  </button>
                )}
                <button onClick={() => deleteGoal(g.id)} className="p-1 text-seal-muted hover:text-red-400">
                  <Trash2 size={12} />
                </button>
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Add goal form */}
      {showAdd && (
        <div className="border-t border-seal-border p-3 space-y-2 bg-seal-surface shrink-0">
          <input value={form.title} onChange={e => setForm(f => ({ ...f, title: e.target.value }))}
            placeholder="Meta *" className="w-full bg-seal-bg border border-seal-border rounded-lg px-3 py-1.5 text-sm text-slate-200 placeholder-seal-muted outline-none" />
          <textarea value={form.description} onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
            placeholder="Detalle (opcional)" rows={2}
            className="w-full bg-seal-bg border border-seal-border rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-seal-muted outline-none resize-none" />
          <div className="flex gap-2 items-center">
            <label className="text-xs text-seal-muted shrink-0">Prioridad</label>
            <input type="range" min={1} max={10} value={form.priority} onChange={e => setForm(f => ({ ...f, priority: +e.target.value }))} className="flex-1" />
            <span className="text-xs text-slate-300 w-4">{form.priority}</span>
            <input type="date" value={form.due_date} onChange={e => setForm(f => ({ ...f, due_date: e.target.value }))}
              className="bg-seal-bg border border-seal-border rounded-lg px-2 py-1 text-xs text-slate-200 outline-none" />
          </div>
          <div className="flex gap-2 justify-end">
            <button onClick={() => setShowAdd(false)} className="px-3 py-1 text-sm text-seal-muted hover:text-slate-300">Cancelar</button>
            <button onClick={createGoal} disabled={!form.title.trim()}
              className="px-3 py-1 text-sm bg-blue-600 hover:bg-blue-500 rounded-lg disabled:opacity-30 transition-colors">Agregar meta</button>
          </div>
        </div>
      )}

      {!showAdd && (
        <div className="p-3 border-t border-seal-border shrink-0">
          <button onClick={() => setShowAdd(true)}
            className="w-full flex items-center justify-center gap-2 py-2 text-sm text-seal-muted hover:text-slate-300 border border-dashed border-seal-border rounded-lg hover:border-slate-500 transition-colors">
            <Plus size={14} /> Agregar meta
          </button>
        </div>
      )}
    </div>
  )
}
