/**
 * Shared error-message extractor for axios/FastAPI errors.
 *
 * Normalises both FastAPI detail shapes:
 *  - Our own string messages: `{ detail: "..." }`
 *  - Pydantic 422 arrays:     `{ detail: [{ msg, loc, type }, ...] }`
 *
 * Also falls back to `error.message` for generic JS errors, and a
 * final `"Request failed"` if nothing parses.
 *
 * Ported from upstream PiTun v1.4.7 — eliminates every ad-hoc
 * `extractAxiosError` copy scattered across components.
 */
export function apiError(err: unknown): string {
  if (typeof err === 'object' && err !== null) {
    const e = err as {
      response?: { data?: { detail?: unknown } }
      message?: string
    }
    const detail = e.response?.data?.detail

    if (typeof detail === 'string') return detail

    if (Array.isArray(detail)) {
      return detail
        .map((d: { msg?: string; loc?: unknown; type?: string }) => {
          const msg = d?.msg ?? ''
          const loc = d?.loc ? ' (' + JSON.stringify(d.loc) + ')' : ''
          return msg + loc
        })
        .filter(Boolean)
        .join('; ')
    }

    if (e.message) return e.message
  }
  if (err instanceof Error) return err.message
  return 'Request failed'
}
