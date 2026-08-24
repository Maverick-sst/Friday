async function handle<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`
    try {
      const body = await res.json()
      if (body?.detail?.message) message = body.detail.message
      else if (body?.detail) message = typeof body.detail === 'string' ? body.detail : JSON.stringify(body.detail)
    } catch { /* keep default */ }
    throw new Error(message)
  }
  return res.json() as Promise<T>
}

export const api = {
  get: <T>(path: string): Promise<T> => fetch(path).then((r) => handle<T>(r)),
  post: <T>(path: string, body?: unknown): Promise<T> =>
    fetch(path, {
      method: 'POST',
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    }).then((r) => handle<T>(r)),
  put: <T>(path: string, body: unknown): Promise<T> =>
    fetch(path, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(
      (r) => handle<T>(r),
    ),
  del: <T>(path: string): Promise<T> => fetch(path, { method: 'DELETE' }).then((r) => handle<T>(r)),
}
