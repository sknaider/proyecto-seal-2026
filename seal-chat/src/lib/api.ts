const BASE = '';

function getToken(): string | null {
  return localStorage.getItem('seal_token');
}

function headers(): HeadersInit {
  const h: HeadersInit = { 'Content-Type': 'application/json' };
  const t = getToken();
  if (t) h['Authorization'] = `Bearer ${t}`;
  return h;
}

async function request<T>(path: string, opts?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { headers: headers(), ...opts });
  if (res.status === 401) {
    localStorage.removeItem('seal_token');
    window.location.href = '/login';
    throw new Error('Unauthorized');
  }
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

export const api = {
  login: (username: string, password: string) =>
    request<{ token: string; user: any }>('/api/auth/login', {
      method: 'POST',
      body: JSON.stringify({ username, password }),
    }),

  me: () => request<{ user: any }>('/api/auth/me'),

  channels: () => request<{ channels: any[] }>('/api/chat/channels'),

  messages: (channel: string, limit = 50, before?: string) => {
    let url = `/api/chat/messages?channel=${encodeURIComponent(channel)}&limit=${limit}`;
    if (before) url += `&before=${encodeURIComponent(before)}`;
    return request<{ messages: any[] }>(url);
  },

  send: (message: string, channel: string, type = 'text', reply_to?: string) =>
    request<{ message: any }>('/api/chat/send', {
      method: 'POST',
      body: JSON.stringify({ message, channel, type, reply_to }),
    }),

  createDM: (target: string) =>
    request<{ channel: any }>('/api/chat/dm', {
      method: 'POST',
      body: JSON.stringify({ target }),
    }),

  search: (q: string) =>
    request<{ results: any[] }>(`/api/chat/search?q=${encodeURIComponent(q)}`),

  upload: async (file: File, channel: string) => {
    const formData = new FormData();
    formData.append('file', file);
    formData.append('channel', channel);
    const t = getToken();
    const res = await fetch(`${BASE}/api/upload`, {
      method: 'POST',
      headers: t ? { Authorization: `Bearer ${t}` } : {},
      body: formData,
    });
    if (!res.ok) throw new Error('Upload failed');
    return res.json();
  },
};
