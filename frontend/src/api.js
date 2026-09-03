/**
 * API client with credentials and JSON helpers.
 */

const API_BASE = '/api/v1';

export class ApiError extends Error {
  constructor(message, status, detail = null) {
    super(message);
    this.status = status;
    this.detail = detail;
  }
}

export async function fetchApi(endpoint, options = {}) {
  const url = endpoint.startsWith('http') ? endpoint : `${API_BASE}${endpoint.startsWith('/') ? endpoint : `/${endpoint}`}`;
  
  const headers = {
    'Accept': 'application/json',
    ...(options.headers || {}),
  };

  if (options.body && !(options.body instanceof FormData)) {
    headers['Content-Type'] = 'application/json';
    options.body = JSON.stringify(options.body);
  }

  const response = await fetch(url, {
    credentials: 'include',
    ...options,
    headers,
  });

  if (response.status === 401) {
    // Only redirect if not already on the login page
    if (!window.location.pathname.startsWith('/login')) {
      window.location.href = '/login';
    }
  }

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const data = await response.json();
      detail = data.detail || data.error || detail;
    } catch {
      // ignore
    }
    throw new ApiError(detail, response.status, detail);
  }

  // If response has no content (e.g. 204 or empty response)
  const contentType = response.headers.get('content-type');
  if (contentType && contentType.includes('application/json')) {
    return response.json();
  }
  return response.text();
}

export const api = {
  // Auth
  login: (username, password) => fetchApi('/auth/login', { method: 'POST', body: { username, password } }),
  logout: () => fetchApi('/auth/logout', { method: 'POST' }),
  me: () => fetchApi('/auth/me'),

  // Dashboard & Stats
  getDashboardStats: () => fetchApi('/dashboard/stats'),
  getHealth: () => fetchApi('/health'),

  // Jobs & Queue
  getJobs: (params = {}) => {
    const query = new URLSearchParams(params).toString();
    return fetchApi(`/jobs${query ? `?${query}` : ''}`);
  },
  getJob: (id) => fetchApi(`/jobs/${id}`),
  releaseJob: (id, reason) => fetchApi(`/jobs/${id}/release`, { method: 'POST', body: { reason } }),
  denyJob: (id, reason) => fetchApi(`/jobs/${id}/deny`, { method: 'POST', body: { reason } }),
  requestContent: (id, reason) => fetchApi(`/jobs/${id}/content-requests`, { method: 'POST', body: { reason } }),
  approveContent: (requestId) => fetchApi(`/jobs/content-requests/${requestId}/approve`, { method: 'POST' }),

  // Rules
  getRules: () => fetchApi('/rules'),
  getRule: (id) => fetchApi(`/rules/${id}`),
  createRule: (data) => fetchApi('/rules', { method: 'POST', body: data }),
  updateRule: (id, data) => fetchApi(`/rules/${id}`, { method: 'PUT', body: data }),
  deleteRule: (id, note) => fetchApi(`/rules/${id}`, { method: 'DELETE', body: { note } }),
  tryRule: (rule, sampleText) => fetchApi('/rules/try', { method: 'POST', body: { rule, sample_text: sampleText } }),

  // Validators
  getValidators: () => fetchApi('/validators'),
  getValidator: (id) => fetchApi(`/validators/${id}`),
  createValidator: (data) => fetchApi('/validators', { method: 'POST', body: data }),
  updateValidator: (id, data) => fetchApi(`/validators/${id}`, { method: 'PUT', body: data }),
  deleteValidator: (id, note) => fetchApi(`/validators/${id}`, { method: 'DELETE', body: { note } }),
  tryValidator: (kind, params, sample) => fetchApi('/validators/try', { method: 'POST', body: { kind, params, sample } }),

  // Printers
  getPrinters: () => fetchApi('/printers'),
  createPrinter: (data) => fetchApi('/printers', { method: 'POST', body: data }),
  updatePrinter: (name, data) => fetchApi(`/printers/${name}`, { method: 'PATCH', body: data }),
  deletePrinter: (name, note) => fetchApi(`/printers/${name}`, { method: 'DELETE', body: { note } }),
  adoptPrinter: (name) => fetchApi(`/printers/${name}/adopt`, { method: 'POST' }),

  // Documents & Fingerprints
  getDocuments: () => fetchApi('/documents'),
  registerDocument: (formData) => fetchApi('/documents', { method: 'POST', body: formData }),
  deleteDocument: (id) => fetchApi(`/documents/${id}`, { method: 'DELETE' }),

  // Audit
  getAuditLog: (limit = 200) => fetchApi(`/audit?limit=${limit}`),

  // Users
  getUsers: () => fetchApi('/users'),
  createUser: (data) => fetchApi('/users', { method: 'POST', body: data }),
  updateUser: (id, data) => fetchApi(`/users/${id}`, { method: 'PATCH', body: data }),
};
