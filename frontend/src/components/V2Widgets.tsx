import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Check, X, TrendingUp, Activity, Lightbulb } from 'lucide-react'
import { clsx } from 'clsx'
import { slaApi, trafficApi, suggestionsApi } from '@/api/client'

export function V2Widgets() {
  return (
    <div className="grid gap-4 lg:grid-cols-3">
      <SLAWidget />
      <TrafficWidget />
      <SuggestionsWidget />
    </div>
  )
}

function SLAWidget() {
  const { data: sla = [] } = useQuery({
    queryKey: ['sla-summary'],
    queryFn: () => slaApi.summary(),
    refetchInterval: 60_000,
  })

  if (sla.length === 0) return null

  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900/50 p-4 space-y-3">
      <div className="flex items-center gap-2">
        <Activity className="h-4 w-4 text-emerald-400" />
        <h3 className="text-sm font-semibold text-gray-200">Node SLA (7d)</h3>
      </div>
      <div className="space-y-1.5">
        {sla.slice(0, 5).map((s: any) => (
          <div key={s.node_id} className="flex items-center gap-2 text-xs">
            <span className="flex-1 text-gray-300 truncate">{s.node_name}</span>
            <span className={clsx(
              'font-mono font-medium',
              s.uptime_7d >= 99 ? 'text-green-400' :
              s.uptime_7d >= 90 ? 'text-yellow-400' : 'text-red-400'
            )}>
              {s.uptime_7d?.toFixed(1)}%
            </span>
            {s.avg_latency_7d && (
              <span className="text-gray-500 font-mono">{Math.round(s.avg_latency_7d)}ms</span>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

function TrafficWidget() {
  const { data: traffic = [] } = useQuery({
    queryKey: ['traffic-summary'],
    queryFn: () => trafficApi.summary(24),
    refetchInterval: 30_000,
  })

  if (traffic.length === 0) return null

  const totalSent = traffic.reduce((sum: number, t: any) => sum + (t.total_bytes_sent || 0), 0)
  const totalRecv = traffic.reduce((sum: number, t: any) => sum + (t.total_bytes_recv || 0), 0)

  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900/50 p-4 space-y-3">
      <div className="flex items-center gap-2">
        <TrendingUp className="h-4 w-4 text-blue-400" />
        <h3 className="text-sm font-semibold text-gray-200">Traffic (24h)</h3>
      </div>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <div className="text-xs text-gray-500">Download</div>
          <div className="text-lg font-bold text-green-400">{formatBytes(totalRecv)}</div>
        </div>
        <div>
          <div className="text-xs text-gray-500">Upload</div>
          <div className="text-lg font-bold text-blue-400">{formatBytes(totalSent)}</div>
        </div>
      </div>
      {traffic.length > 1 && (
        <div className="space-y-1">
          <div className="text-xs text-gray-500 mb-1">Top consumers</div>
          {traffic.slice(0, 3).map((t: any) => (
            <div key={t.device_id ?? 'agg'} className="flex justify-between text-xs">
              <span className="text-gray-300 truncate">{t.device_name}</span>
              <span className="text-gray-400 font-mono">{formatBytes((t.total_bytes_sent || 0) + (t.total_bytes_recv || 0))}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function SuggestionsWidget() {
  const qc = useQueryClient()
  const { data: suggestions = [] } = useQuery({
    queryKey: ['suggestions'],
    queryFn: () => suggestionsApi.list(),
    refetchInterval: 60_000,
  })

  const accept = useMutation({
    mutationFn: (id: number) => suggestionsApi.accept(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['suggestions'] }),
  })

  const dismiss = useMutation({
    mutationFn: (id: number) => suggestionsApi.dismiss(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['suggestions'] }),
  })

  if (suggestions.length === 0) return null

  return (
    <div className="rounded-xl border border-gray-800 bg-gray-900/50 p-4 space-y-3">
      <div className="flex items-center gap-2">
        <Lightbulb className="h-4 w-4 text-yellow-400" />
        <h3 className="text-sm font-semibold text-gray-200">Suggestions</h3>
        <span className="ml-auto text-xs text-gray-500">{suggestions.length} pending</span>
      </div>
      <div className="space-y-2 max-h-48 overflow-y-auto">
        {suggestions.slice(0, 5).map((s: any) => (
          <div key={s.id} className="rounded-lg bg-gray-800/50 p-2.5">
            <div className="flex items-start justify-between gap-2">
              <div className="flex-1 min-w-0">
                <div className="text-sm text-gray-200 font-mono truncate">{s.domain}</div>
                <div className="text-xs text-gray-500 truncate">{s.reason}</div>
              </div>
              <div className="flex gap-1 shrink-0">
                <button
                  onClick={() => accept.mutate(s.id)}
                  disabled={accept.isPending}
                  className="rounded p-1 text-green-400 hover:bg-green-900/30 transition-colors"
                  title="Accept (create rule)"
                >
                  <Check className="h-3.5 w-3.5" />
                </button>
                <button
                  onClick={() => dismiss.mutate(s.id)}
                  className="rounded p-1 text-gray-500 hover:text-red-400 hover:bg-gray-700 transition-colors"
                  title="Dismiss"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

function formatBytes(bytes: number): string {
  if (!bytes || bytes === 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(1024))
  return (bytes / Math.pow(1024, i)).toFixed(1) + ' ' + units[i]
}
