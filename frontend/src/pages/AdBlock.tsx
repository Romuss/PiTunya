import { useState } from 'react'
import { Shield, Plus, Trash2, RefreshCw } from 'lucide-react'
import { clsx } from 'clsx'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { adblockApi } from '@/api/client'
import { ModalShell } from '@/components/ModalShell'

export function AdBlock() {
  const qc = useQueryClient()
  const [showAddList, setShowAddList] = useState(false)
  const [refreshingId, setRefreshingId] = useState<number | null>(null)

  const { data: lists = [] } = useQuery({
    queryKey: ['adblock-lists'],
    queryFn: () => adblockApi.listLists(),
    refetchInterval: 60_000,
  })

  const refreshList = useMutation({
    mutationFn: (id: number) => adblockApi.refreshList(id),
    onMutate: (id) => setRefreshingId(id),
    onSettled: () => { setRefreshingId(null); qc.invalidateQueries({ queryKey: ['adblock-lists'] }) },
  })

  const deleteList = useMutation({
    mutationFn: (id: number) => adblockApi.deleteList(id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['adblock-lists'] }) },
  })

  return (
    <div className="p-4 sm:p-6 space-y-5">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-2">
          <Shield className="h-5 w-5 text-brand-500" />
          <h1 className="text-xl font-bold text-gray-100">Ad Blocklists</h1>
        </div>
        <button onClick={() => setShowAddList(true)} className="flex items-center gap-1.5 rounded-lg bg-brand-600 px-3 py-2 text-sm font-medium text-white hover:bg-brand-500 transition-colors">
          <Plus className="h-4 w-4" /> Add List
        </button>
      </div>

      <p className="text-sm text-gray-500">
        Manage ad blocking lists. Enabled lists are automatically applied as routing rules in xray (domain → blackhole). Enable/disable lists with the toggle.
      </p>

      {/* Blocklists */}
      <div className="space-y-2">
        {lists.length === 0 ? (
          <div className="rounded-xl border border-gray-800 bg-gray-900/50 p-8 text-center">
            <Shield className="h-10 w-10 text-gray-700 mx-auto mb-3" />
            <p className="text-sm text-gray-500">No blocklists yet. Add one to start blocking ads.</p>
          </div>
        ) : (
          lists.map((lst: any) => (
            <div key={lst.id} className="flex items-center gap-3 rounded-xl border border-gray-800 bg-gray-900 p-4">
              <div className={clsx('flex items-center justify-center w-9 h-9 rounded-lg shrink-0', lst.enabled ? 'bg-emerald-900/40' : 'bg-gray-800')}>
                <Shield className={clsx('h-4 w-4', lst.enabled ? 'text-emerald-400' : 'text-gray-500')} />
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-sm text-gray-200 font-medium truncate">{lst.name}</span>
                  <span className={clsx('rounded px-1.5 py-0.5 text-xs font-medium', lst.enabled ? 'bg-emerald-900/60 text-emerald-300' : 'bg-gray-800 text-gray-400')}>
                    {lst.enabled ? 'ON' : 'OFF'}
                  </span>
                </div>
                <div className="text-xs text-gray-500 truncate">{lst.url}</div>
                <div className="text-xs text-gray-600 mt-0.5">
                  {lst.entry_count?.toLocaleString() ?? 0} domains
                  {lst.last_updated && ' · ' + new Date(lst.last_updated.endsWith('Z') ? lst.last_updated : lst.last_updated + 'Z').toLocaleDateString('ru-RU')}
                </div>
              </div>
              <button
                onClick={async () => {
                  await fetch(`/api/adblock/lists/${lst.id}`, {
                    method: 'PATCH',
                    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('pitun_token')}` },
                    body: JSON.stringify({ enabled: !lst.enabled })
                  })
                  qc.invalidateQueries({ queryKey: ['adblock-lists'] })
                }}
                className="rounded-full px-3 py-1.5 text-xs font-medium transition-colors bg-gray-800 text-gray-300 hover:bg-gray-700"
              >
                {lst.enabled ? 'Disable' : 'Enable'}
              </button>
              <button
                onClick={() => refreshList.mutate(lst.id)}
                disabled={refreshingId === lst.id}
                title="Download/refresh"
                className="rounded-lg p-2 text-gray-500 hover:text-brand-400 hover:bg-gray-800 transition-colors disabled:opacity-40"
              >
                <RefreshCw className={clsx('h-4 w-4', refreshingId === lst.id && 'animate-spin')} />
              </button>
              <button
                onClick={() => { if (confirm(`Delete "${lst.name}"?`)) deleteList.mutate(lst.id) }}
                className="rounded-lg p-2 text-gray-500 hover:text-red-400 hover:bg-gray-800 transition-colors"
              >
                <Trash2 className="h-4 w-4" />
              </button>
            </div>
          ))
        )}
      </div>

      {showAddList && (
        <AddListModal onSave={async (d) => { await adblockApi.createList(d); qc.invalidateQueries({ queryKey: ['adblock-lists'] }); setShowAddList(false) }} onCancel={() => setShowAddList(false)} />
      )}
    </div>
  )
}

function AddListModal({ onSave, onCancel }: { onSave: (data: { name: string; url: string; format?: string; enabled?: boolean }) => void; onCancel: () => void }) {
  const [name, setName] = useState('')
  const [url, setUrl] = useState('')
  const [format, setFormat] = useState('hosts')
  return (
    <ModalShell onClose={onCancel} labelledBy="adblock-list-modal">
      <div className="w-full max-w-md rounded-2xl bg-gray-950 border border-gray-800 p-6">
        <h2 className="text-base font-semibold text-gray-100 mb-4">Add Blocklist</h2>
        <form onSubmit={(e) => { e.preventDefault(); onSave({ name, url, format, enabled: true }) }} className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-gray-400 mb-1">Name</label>
            <input value={name} onChange={(e) => setName(e.target.value)} required autoFocus placeholder="My List" className="w-full rounded bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-none" />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-400 mb-1">URL</label>
            <input value={url} onChange={(e) => setUrl(e.target.value)} required placeholder="https://..." className="w-full rounded bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-none" />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-400 mb-1">Format</label>
            <select value={format} onChange={(e) => setFormat(e.target.value)} className="w-full rounded bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100">
              <option value="hosts">Hosts file (0.0.0.0 domain)</option>
              <option value="domain">Domain list (one per line)</option>
            </select>
          </div>
          <div className="flex justify-end gap-3 pt-2 border-t border-gray-800">
            <button type="button" onClick={onCancel} className="rounded-lg px-4 py-2 text-sm text-gray-400 hover:text-gray-100 hover:bg-gray-800 transition-colors">Cancel</button>
            <button type="submit" className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-500 transition-colors">Add</button>
          </div>
        </form>
      </div>
    </ModalShell>
  )
}
