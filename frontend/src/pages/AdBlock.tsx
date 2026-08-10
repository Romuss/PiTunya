import { useState } from 'react'
import { Shield, Plus, Trash2, RefreshCw } from 'lucide-react'
import { clsx } from 'clsx'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { adblockApi } from '@/api/client'
import { useConfirm } from '@/components/ConfirmModal'
import { ModalShell } from '@/components/ModalShell'

export function AdBlock() {
  const qc = useQueryClient()
  const confirm = useConfirm()
  const [showAddRule, setShowAddRule] = useState(false)
  const [showAddList, setShowAddList] = useState(false)
  const [refreshingId, setRefreshingId] = useState<number | null>(null)

  const { data: stats } = useQuery({
    queryKey: ['adblock-stats'],
    queryFn: () => adblockApi.stats(),
    refetchInterval: 30_000,
  })

  const { data: rules = [] } = useQuery({
    queryKey: ['adblock-rules'],
    queryFn: () => adblockApi.listRules(),
  })

  const { data: lists = [] } = useQuery({
    queryKey: ['adblock-lists'],
    queryFn: () => adblockApi.listLists(),
    refetchInterval: 60_000,
  })

  const createRule = useMutation({
    mutationFn: (data: { domain_pattern: string; rule_type?: string }) => adblockApi.createRule(data),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['adblock-rules'] }); qc.invalidateQueries({ queryKey: ['adblock-stats'] }); setShowAddRule(false) },
  })

  const deleteRule = useMutation({
    mutationFn: (id: number) => adblockApi.deleteRule(id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['adblock-rules'] }); qc.invalidateQueries({ queryKey: ['adblock-stats'] }) },
  })

  const refreshList = useMutation({
    mutationFn: (id: number) => adblockApi.refreshList(id),
    onMutate: (id) => setRefreshingId(id),
    onSettled: () => { setRefreshingId(null); qc.invalidateQueries({ queryKey: ['adblock-lists'] }); qc.invalidateQueries({ queryKey: ['adblock-stats'] }) },
  })

  const deleteList = useMutation({
    mutationFn: (id: number) => adblockApi.deleteList(id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['adblock-lists'] }); qc.invalidateQueries({ queryKey: ['adblock-stats'] }); qc.invalidateQueries({ queryKey: ['adblock-rules'] }) },
  })

  return (
    <div className="p-4 sm:p-6 space-y-5">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-2">
          <Shield className="h-5 w-5 text-brand-500" />
          <h1 className="text-xl font-bold text-gray-100">Ad Blocking</h1>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => setShowAddRule(true)} className="flex items-center gap-1.5 rounded-lg bg-gray-700 px-3 py-2 text-sm text-gray-200 hover:bg-gray-600 transition-colors">
            <Plus className="h-4 w-4" /> Add Rule
          </button>
          <button onClick={() => setShowAddList(true)} className="flex items-center gap-1.5 rounded-lg bg-brand-600 px-3 py-2 text-sm font-medium text-white hover:bg-brand-500 transition-colors">
            <Plus className="h-4 w-4" /> Add List
          </button>
        </div>
      </div>

      {/* Stats */}
      {stats && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
            <div className="text-2xl font-bold text-gray-100">{stats.total_rules?.toLocaleString() ?? 0}</div>
            <div className="text-xs text-gray-500">Total Rules</div>
          </div>
          <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
            <div className="text-2xl font-bold text-green-400">{stats.enabled_rules?.toLocaleString() ?? 0}</div>
            <div className="text-xs text-gray-500">Enabled</div>
          </div>
          <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
            <div className="text-2xl font-bold text-blue-400">{stats.blocklists ?? 0}</div>
            <div className="text-xs text-gray-500">Blocklists</div>
          </div>
          <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
            <div className="text-2xl font-bold text-brand-400">{stats.total_entries?.toLocaleString() ?? 0}</div>
            <div className="text-xs text-gray-500">Total Entries</div>
          </div>
        </div>
      )}

      {/* Blocklists */}
      <div className="rounded-xl border border-gray-800 bg-gray-900/50 p-4 space-y-3">
        <h2 className="text-sm font-semibold text-gray-200">Blocklists</h2>
        {lists.length === 0 ? (
          <p className="text-sm text-gray-500">No blocklists. Add one to start blocking ads.</p>
        ) : (
          <div className="space-y-2">
            {lists.map((lst: any) => (
              <div key={lst.id} className="flex items-center gap-3 rounded-lg border border-gray-800 bg-gray-900 p-3">
                <div className={clsx('h-2 w-2 rounded-full', lst.enabled ? 'bg-green-400' : 'bg-gray-600')} />
                <div className="flex-1 min-w-0">
                  <div className="text-sm text-gray-200 font-medium">{lst.name}</div>
                  <div className="text-xs text-gray-500 truncate">{lst.url}</div>
                </div>
                <div className="text-xs text-gray-500 font-mono">{lst.entry_count?.toLocaleString() ?? 0} domains</div>
                <div className="text-xs text-gray-500">
                  {lst.last_updated ? new Date(lst.last_updated.endsWith('Z') ? lst.last_updated : lst.last_updated + 'Z').toLocaleDateString() : 'Never'}
                </div>
                <button
                  onClick={() => refreshList.mutate(lst.id)}
                  disabled={refreshingId === lst.id}
                  title="Refresh list"
                  className="rounded p-1.5 text-gray-500 hover:text-brand-400 hover:bg-gray-800 transition-colors disabled:opacity-40"
                >
                  <RefreshCw className={clsx('h-4 w-4', refreshingId === lst.id && 'animate-spin')} />
                </button>
                <button
                  onClick={async () => {
                    const ok = await confirm({ title: `Delete "${lst.name}"?`, body: 'All rules from this list will be removed.', confirmLabel: 'Delete', danger: true })
                    if (ok) deleteList.mutate(lst.id)
                  }}
                  className="rounded p-1.5 text-gray-500 hover:text-red-400 hover:bg-gray-800 transition-colors"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Manual Rules */}
      <div className="rounded-xl border border-gray-800 bg-gray-900/50 p-4 space-y-3">
        <h2 className="text-sm font-semibold text-gray-200">Manual Rules</h2>
        {rules.length === 0 ? (
          <p className="text-sm text-gray-500">No manual rules. Add a domain to block or allow.</p>
        ) : (
          <div className="rounded-lg border border-gray-800 bg-gray-900 max-h-96 overflow-y-auto divide-y divide-gray-800/50">
            {rules.slice(0, 200).map((r: any) => (
              <div key={r.id} className="flex items-center gap-3 px-3 py-2 hover:bg-gray-800/50">
                <span className={clsx('rounded px-1.5 py-0.5 text-xs font-mono font-medium', r.rule_type === 'block' ? 'bg-red-900/60 text-red-300' : 'bg-green-900/60 text-green-300')}>
                  {r.rule_type}
                </span>
                <span className="flex-1 text-sm text-gray-200 font-mono truncate">{r.domain_pattern}</span>
                <span className="text-xs text-gray-600">{r.source}</span>
                <button
                  onClick={() => deleteRule.mutate(r.id)}
                  className="rounded p-1 text-gray-600 hover:text-red-400 transition-colors"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            ))}
            {rules.length > 200 && (
              <div className="px-3 py-2 text-xs text-gray-500 text-center">Showing 200 of {rules.length} rules</div>
            )}
          </div>
        )}
      </div>

      {/* Add Rule Modal */}
      {showAddRule && (
        <AddRuleModal onSave={(d) => createRule.mutate(d)} onCancel={() => setShowAddRule(false)} loading={createRule.isPending} />
      )}
      {/* Add List Modal */}
      {showAddList && (
        <AddListModal onSave={async (d) => { await adblockApi.createList(d); qc.invalidateQueries({ queryKey: ['adblock-lists'] }); setShowAddList(false) }} onCancel={() => setShowAddList(false)} />
      )}
    </div>
  )
}

function AddRuleModal({ onSave, onCancel, loading }: { onSave: (data: { domain_pattern: string; rule_type?: string }) => void; onCancel: () => void; loading?: boolean }) {
  const [domain, setDomain] = useState('')
  const [ruleType, setRuleType] = useState('block')
  return (
    <ModalShell onClose={onCancel} labelledBy="adblock-rule-modal">
      <div className="w-full max-w-md rounded-2xl bg-gray-950 border border-gray-800 p-6">
        <h2 className="text-base font-semibold text-gray-100 mb-4">Add AdBlock Rule</h2>
        <form onSubmit={(e) => { e.preventDefault(); onSave({ domain_pattern: domain, rule_type: ruleType }) }} className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-gray-400 mb-1">Domain</label>
            <input value={domain} onChange={(e) => setDomain(e.target.value)} required autoFocus placeholder="ads.example.com"
              className="w-full rounded bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-none" />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-400 mb-1">Type</label>
            <select value={ruleType} onChange={(e) => setRuleType(e.target.value)} className="w-full rounded bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100">
              <option value="block">Block</option>
              <option value="allow">Allow (whitelist)</option>
            </select>
          </div>
          <div className="flex justify-end gap-3 pt-2 border-t border-gray-800">
            <button type="button" onClick={onCancel} className="rounded-lg px-4 py-2 text-sm text-gray-400 hover:text-gray-100 hover:bg-gray-800 transition-colors">Cancel</button>
            <button type="submit" disabled={loading} className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-500 disabled:opacity-50 transition-colors">{loading ? 'Saving…' : 'Add Rule'}</button>
          </div>
        </form>
      </div>
    </ModalShell>
  )
}

function AddListModal({ onSave, onCancel }: { onSave: (data: { name: string; url: string; format?: string; enabled?: boolean }) => void; onCancel: () => void }) {
  const [name, setName] = useState('')
  const [url, setUrl] = useState('')
  const [format, setFormat] = useState('hosts')
  const [enabled, setEnabled] = useState(true)
  return (
    <ModalShell onClose={onCancel} labelledBy="adblock-list-modal">
      <div className="w-full max-w-md rounded-2xl bg-gray-950 border border-gray-800 p-6">
        <h2 className="text-base font-semibold text-gray-100 mb-4">Add Blocklist</h2>
        <form onSubmit={(e) => { e.preventDefault(); onSave({ name, url, format, enabled }) }} className="space-y-4">
          <div>
            <label className="block text-xs font-medium text-gray-400 mb-1">Name</label>
            <input value={name} onChange={(e) => setName(e.target.value)} required autoFocus placeholder="My Blocklist"
              className="w-full rounded bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-none" />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-400 mb-1">URL</label>
            <input value={url} onChange={(e) => setUrl(e.target.value)} required placeholder="https://example.com/hosts"
              className="w-full rounded bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100 focus:border-brand-500 focus:outline-none" />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-400 mb-1">Format</label>
            <select value={format} onChange={(e) => setFormat(e.target.value)} className="w-full rounded bg-gray-800 border border-gray-700 px-3 py-1.5 text-sm text-gray-100">
              <option value="hosts">Hosts file (0.0.0.0 domain)</option>
              <option value="domain">Domain list</option>
            </select>
          </div>
          <div className="flex items-center gap-2">
            <input type="checkbox" id="list-enabled" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} className="rounded border-gray-600 bg-gray-800 text-brand-500" />
            <label htmlFor="list-enabled" className="text-sm text-gray-300">Enabled</label>
          </div>
          <div className="flex justify-end gap-3 pt-2 border-t border-gray-800">
            <button type="button" onClick={onCancel} className="rounded-lg px-4 py-2 text-sm text-gray-400 hover:text-gray-100 hover:bg-gray-800 transition-colors">Cancel</button>
            <button type="submit" className="rounded-lg bg-brand-600 px-4 py-2 text-sm font-medium text-white hover:bg-brand-500 transition-colors">Add List</button>
          </div>
        </form>
      </div>
    </ModalShell>
  )
}
