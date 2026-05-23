import { useState, useEffect, useCallback } from 'react'
import { API } from '../App'
import { FileText, Play, Plus, Trash2, ToggleLeft, ToggleRight, ChevronDown, ChevronUp } from 'lucide-react'

interface Skill {
  id: number; name: string; description?: string; trigger_phrase?: string
  prompt_template: string; category?: string; enabled: boolean; use_count: number
}

interface Props { onUseSkill: (prompt: string) => void }

export default function SkillsView({ onUseSkill }: Props) {
  const [skills, setSkills] = useState<Skill[]>([])
  const [showAdd, setShowAdd] = useState(false)
  const [showImport, setShowImport] = useState(false)
  const [expanded, setExpanded] = useState<number | null>(null)
  const [runVars, setRunVars] = useState<Record<number, Record<string, string>>>({})
  const [form, setForm] = useState({ name: '', description: '', trigger_phrase: '', prompt_template: '', category: '' })
  const [importForm, setImportForm] = useState({ path: '', content: '', name: '', overwrite: false })
  const [importMessage, setImportMessage] = useState('')

  const load = useCallback(async () => {
    const r = await fetch(`${API}/api/skills`)
    const d = await r.json()
    setSkills(d.skills || [])
  }, [])

  useEffect(() => { load() }, [load])

  async function createSkill() {
    if (!form.name.trim() || !form.prompt_template.trim()) return
    await fetch(`${API}/api/skills`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...form, enabled: true }),
    })
    setForm({ name: '', description: '', trigger_phrase: '', prompt_template: '', category: '' })
    setShowAdd(false)
    load()
  }

  async function importSkillMd() {
    if (!importForm.path.trim() && !importForm.content.trim()) return
    setImportMessage('')
    const body: Record<string, string | boolean> = { overwrite: importForm.overwrite }
    if (importForm.path.trim()) body.path = importForm.path.trim()
    if (importForm.content.trim()) body.content = importForm.content
    if (importForm.name.trim()) body.name = importForm.name.trim()
    const r = await fetch(`${API}/api/skills/import-skill-md`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    const d = await r.json()
    if (!r.ok) {
      setImportMessage(d.detail || 'No se pudo importar')
      return
    }
    setImportForm({ path: '', content: '', name: '', overwrite: false })
    setShowImport(false)
    setImportMessage(`Importado: ${d.name}`)
    load()
  }

  async function toggleSkill(s: Skill) {
    await fetch(`${API}/api/skills/${s.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: !s.enabled }),
    })
    load()
  }

  async function deleteSkill(id: number) {
    await fetch(`${API}/api/skills/${id}`, { method: 'DELETE' })
    setSkills(sk => sk.filter(s => s.id !== id))
  }

  async function runSkill(skill: Skill) {
    const vars = runVars[skill.id] || {}
    const r = await fetch(`${API}/api/skills/${skill.id}/run`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(vars),
    })
    const d = await r.json()
    if (d.prompt) onUseSkill(d.prompt)
  }

  // Extract template variables like {name}
  function extractVars(template: string): string[] {
    return [...new Set([...template.matchAll(/\{(\w+)\}/g)].map(m => m[1]))]
  }

  return (
    <div className="flex flex-col h-full">
      <div className="flex-1 overflow-y-auto p-3 space-y-2">
        {skills.length === 0 && !showAdd && (
          <div className="flex flex-col items-center justify-center h-40 text-seal-muted text-sm gap-2">
            <div className="text-3xl">⚡</div>
            <div>Aún no hay acciones rápidas. Crea una para reutilizar instrucciones.</div>
          </div>
        )}
        {skills.map(s => {
          const vars = extractVars(s.prompt_template)
          const isExpanded = expanded === s.id
          return (
            <div key={s.id} className={`bg-seal-surface border rounded-lg overflow-hidden transition-colors ${s.enabled ? 'border-seal-border' : 'border-seal-border opacity-60'}`}>
              <div className="flex items-center gap-2 p-3">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium text-slate-200 truncate">{s.name}</span>
                    {s.category && <span className="text-xs bg-slate-700 text-slate-400 px-1.5 py-0.5 rounded shrink-0">{s.category}</span>}
                    {s.use_count > 0 && <span className="text-xs text-seal-muted shrink-0">×{s.use_count}</span>}
                  </div>
                  {s.description && <p className="text-xs text-seal-muted mt-0.5 truncate">{s.description}</p>}
                  {s.trigger_phrase && <p className="text-xs text-blue-400/60 mt-0.5">/{s.trigger_phrase}</p>}
                </div>
                <div className="flex items-center gap-1 shrink-0">
                  <button onClick={() => toggleSkill(s)} className="text-seal-muted hover:text-slate-300 p-1">
                    {s.enabled ? <ToggleRight size={18} className="text-blue-400" /> : <ToggleLeft size={18} />}
                  </button>
                  <button onClick={() => setExpanded(isExpanded ? null : s.id)} className="text-seal-muted hover:text-slate-300 p-1">
                    {isExpanded ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
                  </button>
                </div>
              </div>
              {isExpanded && (
                <div className="border-t border-seal-border p-3 space-y-2">
                  <p className="text-xs text-seal-muted font-mono bg-seal-bg p-2 rounded leading-relaxed whitespace-pre-wrap">{s.prompt_template}</p>
                  {vars.length > 0 && (
                    <div className="space-y-1">
                      <p className="text-xs text-seal-muted">Completa los datos:</p>
                      {vars.map(v => (
                        <div key={v} className="flex items-center gap-2">
                          <span className="text-xs text-blue-400 w-20 shrink-0">{'{'+v+'}'}</span>
                          <input
                            value={runVars[s.id]?.[v] || ''}
                            onChange={e => setRunVars(prev => ({
                              ...prev,
                              [s.id]: { ...prev[s.id], [v]: e.target.value }
                            }))}
                            placeholder={v}
                            className="flex-1 bg-seal-bg border border-seal-border rounded px-2 py-1 text-xs text-slate-200 placeholder-seal-muted outline-none"
                          />
                        </div>
                      ))}
                    </div>
                  )}
                  <div className="flex gap-2 justify-end">
                    <button onClick={() => deleteSkill(s.id)} className="flex items-center gap-1 px-2 py-1 text-xs text-seal-muted hover:text-red-400 transition-colors">
                      <Trash2 size={12} /> Borrar
                    </button>
                    <button
                      onClick={() => runSkill(s)}
                      disabled={!s.enabled}
                      className="flex items-center gap-1 px-3 py-1 text-xs bg-blue-600 hover:bg-blue-500 rounded-lg disabled:opacity-30 transition-colors"
                    >
                      <Play size={12} /> Usar en chat
                    </button>
                  </div>
                </div>
              )}
            </div>
          )
        })}
      </div>

      {/* Create skill form */}
      {showImport && (
        <div className="border-t border-seal-border p-3 space-y-2 bg-seal-surface shrink-0">
          <p className="text-xs text-seal-muted font-semibold uppercase tracking-wide">Importar SKILL.md</p>
          <input
            value={importForm.path}
            onChange={e => setImportForm(f => ({ ...f, path: e.target.value }))}
            placeholder="/ruta/al/SKILL.md"
            className="w-full bg-seal-bg border border-seal-border rounded-lg px-3 py-1.5 text-sm text-slate-200 placeholder-seal-muted outline-none"
          />
          <input
            value={importForm.name}
            onChange={e => setImportForm(f => ({ ...f, name: e.target.value }))}
            placeholder="Nombre opcional"
            className="w-full bg-seal-bg border border-seal-border rounded-lg px-3 py-1.5 text-sm text-slate-200 placeholder-seal-muted outline-none"
          />
          <textarea
            value={importForm.content}
            onChange={e => setImportForm(f => ({ ...f, content: e.target.value }))}
            placeholder="O pega aquí el contenido de SKILL.md"
            rows={5}
            className="w-full bg-seal-bg border border-seal-border rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-seal-muted outline-none resize-none font-mono"
          />
          <label className="flex items-center gap-2 text-xs text-seal-muted">
            <input
              type="checkbox"
              checked={importForm.overwrite}
              onChange={e => setImportForm(f => ({ ...f, overwrite: e.target.checked }))}
            />
            Sobrescribir si ya existe
          </label>
          {importMessage && <p className="text-xs text-seal-muted">{importMessage}</p>}
          <div className="flex gap-2 justify-end">
            <button onClick={() => setShowImport(false)} className="px-3 py-1 text-sm text-seal-muted hover:text-slate-300">Cancelar</button>
            <button onClick={importSkillMd} disabled={!importForm.path.trim() && !importForm.content.trim()}
              className="px-3 py-1 text-sm bg-blue-600 hover:bg-blue-500 rounded-lg disabled:opacity-30 transition-colors">Importar</button>
          </div>
        </div>
      )}

      {showAdd && (
        <div className="border-t border-seal-border p-3 space-y-2 bg-seal-surface shrink-0">
          <p className="text-xs text-seal-muted font-semibold uppercase tracking-wide">Nueva acción rápida</p>
          <input value={form.name} onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
            placeholder="Nombre *" className="w-full bg-seal-bg border border-seal-border rounded-lg px-3 py-1.5 text-sm text-slate-200 placeholder-seal-muted outline-none" />
          <input value={form.description} onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
            placeholder="Descripción" className="w-full bg-seal-bg border border-seal-border rounded-lg px-3 py-1.5 text-sm text-slate-200 placeholder-seal-muted outline-none" />
          <textarea value={form.prompt_template} onChange={e => setForm(f => ({ ...f, prompt_template: e.target.value }))}
            placeholder="Instrucción reutilizable * — usa {dato} si necesitas completar algo" rows={3}
            className="w-full bg-seal-bg border border-seal-border rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-seal-muted outline-none resize-none font-mono" />
          <div className="flex gap-2">
            <input value={form.category} onChange={e => setForm(f => ({ ...f, category: e.target.value }))}
              placeholder="Tema" className="flex-1 bg-seal-bg border border-seal-border rounded-lg px-3 py-1.5 text-sm text-slate-200 placeholder-seal-muted outline-none" />
            <input value={form.trigger_phrase} onChange={e => setForm(f => ({ ...f, trigger_phrase: e.target.value }))}
              placeholder="Palabra para activarla" className="flex-1 bg-seal-bg border border-seal-border rounded-lg px-3 py-1.5 text-sm text-slate-200 placeholder-seal-muted outline-none" />
          </div>
          <div className="flex gap-2 justify-end">
            <button onClick={() => setShowAdd(false)} className="px-3 py-1 text-sm text-seal-muted hover:text-slate-300">Cancelar</button>
            <button onClick={createSkill} disabled={!form.name.trim() || !form.prompt_template.trim()}
              className="px-3 py-1 text-sm bg-blue-600 hover:bg-blue-500 rounded-lg disabled:opacity-30 transition-colors">Crear</button>
          </div>
        </div>
      )}

      {!showAdd && !showImport && (
        <div className="grid grid-cols-1 gap-2 p-3 border-t border-seal-border shrink-0 sm:grid-cols-2">
          <button
            onClick={() => { setShowAdd(false); setShowImport(true) }}
            className="flex items-center justify-center gap-2 py-2 text-sm text-seal-muted hover:text-slate-300 border border-dashed border-seal-border rounded-lg hover:border-slate-500 transition-colors"
          >
            <FileText size={14} /> Importar SKILL.md
          </button>
          <button
            onClick={() => { setShowImport(false); setShowAdd(true) }}
            className="flex items-center justify-center gap-2 py-2 text-sm text-seal-muted hover:text-slate-300 border border-dashed border-seal-border rounded-lg hover:border-slate-500 transition-colors"
          >
            <Plus size={14} /> Nueva acción rápida
          </button>
        </div>
      )}
    </div>
  )
}
