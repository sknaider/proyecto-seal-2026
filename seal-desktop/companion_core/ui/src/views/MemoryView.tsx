import { useState, useEffect, useCallback } from 'react'
import { API } from '../App'
import { Search, Plus, Trash2, X } from 'lucide-react'

interface Memory { id: number; agent: string; category: string; content: string; importance: number; created_at: string }
interface Category { category: string; count: number }

const IMPORTANCE_COLORS: Record<number, string> = {
  9: 'text-red-400', 8: 'text-orange-400', 7: 'text-yellow-400',
  6: 'text-blue-400', 5: 'text-slate-400',
}
function impColor(n: number) { return IMPORTANCE_COLORS[n] || 'text-slate-500' }

export default function MemoryView() {
  const [memories, setMemories] = useState<Memory[]>([])
  const [categories, setCategories] = useState<Category[]>([])
  const [q, setQ] = useState('')
  const [catFilter, setCatFilter] = useState('')
  const [showAdd, setShowAdd] = useState(false)
  const [newContent, setNewContent] = useState('')
  const [newCategory, setNewCategory] = useState('')
  const [newImportance, setNewImportance] = useState(5)
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    if (q.trim().length >= 2) {
      const r = await fetch(`${API}/api/search?q=${encodeURIComponent(q)}&limit=30`)
      const d = await r.json()
      setMemories((d.results || []).filter((x: { type: string }) => x.type === 'memory'))
    } else {
      const params = new URLSearchParams({ limit: '40' })
      if (catFilter) params.set('category', catFilter)
      const r = await fetch(`${API}/api/memories?${params}`)
      const d = await r.json()
      setMemories(d.memories || [])
    }
  }, [q, catFilter])

  useEffect(() => {
    fetch(`${API}/api/memories/categories`)
      .then(r => r.json())
      .then(d => setCategories(d.categories || []))
      .catch(() => {})
  }, [])

  useEffect(() => {
    const t = setTimeout(load, 300)
    return () => clearTimeout(t)
  }, [load])

  async function addMemory() {
    if (!newContent.trim()) return
    setLoading(true)
    await fetch(`${API}/api/memories`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ content: newContent, category: newCategory || undefined, importance: newImportance }),
    })
    setNewContent(''); setNewCategory(''); setNewImportance(5); setShowAdd(false)
    load()
    setLoading(false)
  }

  async function deleteMemory(id: number) {
    await fetch(`${API}/api/memories/${id}`, { method: 'DELETE' })
    setMemories(m => m.filter(x => x.id !== id))
  }

  return (
    <div className="flex flex-col h-full">
      {/* Search + filters */}
      <div className="p-3 space-y-2 border-b border-seal-border shrink-0">
        <div className="flex items-center gap-2 bg-seal-surface border border-seal-border rounded-lg px-3 py-1.5">
          <Search size={14} className="text-seal-muted" />
          <input
            value={q}
            onChange={e => setQ(e.target.value)}
            placeholder="Search memories…"
            className="flex-1 bg-transparent text-sm text-slate-200 placeholder-seal-muted outline-none"
          />
          {q && <button onClick={() => setQ('')}><X size={12} className="text-seal-muted" /></button>}
        </div>
        <div className="flex gap-1.5 flex-wrap">
          <button
            onClick={() => setCatFilter('')}
            className={`px-2 py-0.5 text-xs rounded-full border transition-colors ${!catFilter ? 'bg-blue-600 border-blue-600 text-white' : 'border-seal-border text-seal-muted hover:border-slate-500'}`}
          >All</button>
          {categories.map(c => (
            <button
              key={c.category}
              onClick={() => setCatFilter(c.category === catFilter ? '' : c.category)}
              className={`px-2 py-0.5 text-xs rounded-full border transition-colors ${catFilter === c.category ? 'bg-blue-600 border-blue-600 text-white' : 'border-seal-border text-seal-muted hover:border-slate-500'}`}
            >{c.category} <span className="opacity-60">{c.count}</span></button>
          ))}
        </div>
      </div>

      {/* Memory list */}
      <div className="flex-1 overflow-y-auto p-3 space-y-2">
        {memories.map(m => (
          <div key={m.id} className="bg-seal-surface border border-seal-border rounded-lg p-3 group">
            <div className="flex items-start justify-between gap-2">
              <p className="text-sm text-slate-200 leading-relaxed flex-1">{m.content}</p>
              <button
                onClick={() => deleteMemory(m.id)}
                className="opacity-0 group-hover:opacity-100 p-1 text-seal-muted hover:text-red-400 transition-all"
              ><Trash2 size={13} /></button>
            </div>
            <div className="flex gap-2 mt-1.5 items-center">
              {m.category && <span className="text-xs bg-slate-700 text-slate-300 px-1.5 py-0.5 rounded">{m.category}</span>}
              <span className={`text-xs font-medium ${impColor(m.importance)}`}>★{m.importance}</span>
              <span className="text-xs text-seal-muted ml-auto">{m.created_at?.slice(0, 10)}</span>
            </div>
          </div>
        ))}
        {memories.length === 0 && (
          <div className="flex items-center justify-center h-32 text-seal-muted text-sm">No memories yet</div>
        )}
      </div>

      {/* Add memory */}
      {showAdd && (
        <div className="border-t border-seal-border p-3 space-y-2 bg-seal-surface shrink-0">
          <textarea
            value={newContent}
            onChange={e => setNewContent(e.target.value)}
            placeholder="Memory content…"
            rows={3}
            className="w-full bg-seal-bg border border-seal-border rounded-lg px-3 py-2 text-sm text-slate-200 placeholder-seal-muted outline-none resize-none"
          />
          <div className="flex gap-2">
            <input
              value={newCategory}
              onChange={e => setNewCategory(e.target.value)}
              placeholder="Category (optional)"
              className="flex-1 bg-seal-bg border border-seal-border rounded-lg px-3 py-1.5 text-sm text-slate-200 placeholder-seal-muted outline-none"
            />
            <select
              value={newImportance}
              onChange={e => setNewImportance(+e.target.value)}
              className="bg-seal-bg border border-seal-border rounded-lg px-2 py-1.5 text-sm text-slate-200 outline-none"
            >
              {[1,2,3,4,5,6,7,8,9,10].map(n => <option key={n} value={n}>★{n}</option>)}
            </select>
          </div>
          <div className="flex gap-2 justify-end">
            <button onClick={() => setShowAdd(false)} className="px-3 py-1 text-sm text-seal-muted hover:text-slate-300">Cancel</button>
            <button onClick={addMemory} disabled={loading || !newContent.trim()} className="px-3 py-1 text-sm bg-blue-600 hover:bg-blue-500 rounded-lg disabled:opacity-30 transition-colors">Save</button>
          </div>
        </div>
      )}

      {!showAdd && (
        <div className="p-3 border-t border-seal-border shrink-0">
          <button
            onClick={() => setShowAdd(true)}
            className="w-full flex items-center justify-center gap-2 py-2 text-sm text-seal-muted hover:text-slate-300 border border-dashed border-seal-border rounded-lg hover:border-slate-500 transition-colors"
          >
            <Plus size={14} /> Add memory
          </button>
        </div>
      )}
    </div>
  )
}
