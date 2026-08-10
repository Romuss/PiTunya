import { useQuery } from '@tanstack/react-query'
import { Radio, RefreshCw } from 'lucide-react'
import { clsx } from 'clsx'
import { connectionsApi } from '@/api/client'

export function Connections() {
  const { data: conns = [], refetch, isLoading, isFetching } = useQuery({
    queryKey: ['connections'],
    queryFn: () => connectionsApi.snapshot(),
    refetchInterval: 5_000,
  })

  const { data: summary } = useQuery({
    queryKey: ['connections-summary'],
    queryFn: () => connectionsApi.summary(),
    refetchInterval: 5_000,
  })

  return (
    <div className="p-4 sm:p-6 space-y-5">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-2">
          <Radio className="h-5 w-5 text-brand-500" />
          <h1 className="text-xl font-bold text-gray-100">Connections</h1>
          {summary && (
            <span className="text-sm text-gray-500">
              ({summary.total_active ?? conns.length} active)
            </span>
          )}
        </div>
        <button
          onClick={() => refetch()}
          className="flex items-center gap-1.5 rounded-lg bg-gray-800 px-3 py-2 text-sm text-gray-300 hover:bg-gray-700 transition-colors"
        >
          <RefreshCw className={clsx('h-4 w-4', isFetching && 'animate-spin')} />
          Refresh
        </button>
      </div>

      <p className="text-sm text-gray-500">
        Live view of active network connections through PiTun. Auto-refreshes every 5 seconds.
      </p>

      {/* Summary cards */}
      {summary && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <div className="rounded-xl border border-gray-800 bg-gray-900 p-4">
            <div className="text-2xl font-bold text-gray-100">{summary.total_active ?? 0}</div>
            <div className="text-xs text-gray-500">Active</div>
          </div>
          {Object.entries(summary.protocols || {}).map(([proto, count]) => (
            <div key={proto} className="rounded-xl border border-gray-800 bg-gray-900 p-4">
              <div className="text-2xl font-bold text-blue-400">{count as number}</div>
              <div className="text-xs text-gray-500 uppercase">{proto}</div>
            </div>
          ))}
        </div>
      )}

      {/* Connections table */}
      <div className="rounded-xl border border-gray-800 bg-gray-900/50 overflow-hidden">
        {isLoading ? (
          <div className="text-center py-12 text-gray-500">Loading…</div>
        ) : conns.length === 0 ? (
          <div className="text-center py-12 text-gray-500">
            No active connections. Run traffic through PiTun to see them here.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="border-b border-gray-800 bg-gray-900">
                <tr className="text-left text-xs uppercase text-gray-500">
                  <th className="px-3 py-2 font-medium">Device</th>
                  <th className="px-3 py-2 font-medium">Destination</th>
                  <th className="px-3 py-2 font-medium">Country</th>
                  <th className="px-3 py-2 font-medium">Service</th>
                  <th className="px-3 py-2 font-medium">Proto</th>
                  <th className="px-3 py-2 font-medium">State</th>
                  <th className="px-3 py-2 font-medium">Via</th>
                  <th className="px-3 py-2 font-medium text-right">Bytes</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-800/50">
                {conns.slice(0, 200).map((c: any, i: number) => (
                  <tr key={i} className="hover:bg-gray-800/50">
                    <td className="px-3 py-2 text-gray-200 truncate max-w-32">{c.device_name}</td>
                    <td className="px-3 py-2 text-gray-400 font-mono">{c.dst_ip}</td>
                    <td className="px-3 py-2">
                      {c.country ? (
                        <span className="text-xs font-mono text-gray-400">{c.country}</span>
                      ) : (
                        <span className="text-xs text-gray-600">—</span>
                      )}
                    </td>
                    <td className="px-3 py-2 text-gray-400">{c.service || '—'}</td>
                    <td className="px-3 py-2 text-xs font-mono uppercase text-gray-500">{c.protocol}</td>
                    <td className="px-3 py-2">
                      <span className={clsx(
                        'rounded px-1.5 py-0.5 text-xs font-mono font-medium',
                        c.state === 'ESTABLISHED' ? 'bg-green-900/60 text-green-300' : 'bg-gray-800 text-gray-400'
                      )}>
                        {c.state}
                      </span>
                    </td>
                    <td className="px-3 py-2 text-gray-400 text-xs">{c.via_node}</td>
                    <td className="px-3 py-2 text-right text-xs font-mono text-gray-500">
                      {c.bytes > 0 ? (c.bytes / 1024).toFixed(1) + ' KB' : '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {conns.length > 200 && (
              <div className="px-3 py-2 text-xs text-gray-500 text-center">Showing 200 of {conns.length} connections</div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
