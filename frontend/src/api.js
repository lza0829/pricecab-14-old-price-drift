async function request(path, options = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
    ...options,
  })
  if (!res.ok) {
    let detail = await res.text()
    try {
      const j = JSON.parse(detail)
      detail = j.detail || detail
    } catch (_) {}
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail))
  }
  if (res.status === 204) return null
  return res.json()
}

export const api = {
  health: () => request('/api/health'),
  dashboard: () => request('/api/dashboard'),
  skus: () => request('/api/skus'),
  tickets: () => request('/api/tickets'),
  ticket: (id) => request(`/api/tickets/${id}`),
  createTicket: (body) => request('/api/tickets', { method: 'POST', body: JSON.stringify(body) }),
  clock: () => request('/api/apply/clock'),
  setClock: (business_clock) =>
    request('/api/apply/clock', { method: 'PUT', body: JSON.stringify({ business_clock }) }),
  strategy: () => request('/api/apply/strategy'),
  setStrategy: (drift_strategy) =>
    request('/api/apply/strategy', { method: 'PUT', body: JSON.stringify({ drift_strategy }) }),
  applyDue: () => request('/api/apply/due', { method: 'POST' }),
  batches: () => request('/api/apply/batches'),
  batchLines: (id) => request(`/api/apply/batches/${id}/lines`),
  priceBook: () => request('/api/prices/book'),
  history: (sku) => request(sku ? `/api/prices/history?sku_code=${encodeURIComponent(sku)}` : '/api/prices/history'),
  adjustPrice: (body) => request('/api/prices/adjust', { method: 'POST', body: JSON.stringify(body) }),
  verifyPrices: () => request('/api/prices/verify'),
  reconcile: () => request('/api/reconcile/sales'),
}
