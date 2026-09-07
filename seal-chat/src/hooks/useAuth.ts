import { useState, useEffect, useCallback } from 'react';
import { api } from '../lib/api';
import { sealWS } from '../lib/ws';
import type { User } from '../types';

export function useAuth() {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const token = localStorage.getItem('seal_token');
    if (token) {
      api.me()
        .then(res => {
          setUser(res.user);
          sealWS.connect(token);
        })
        .catch(() => localStorage.removeItem('seal_token'))
        .finally(() => setLoading(false));
    } else {
      setLoading(false);
    }
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    setError(null);
    try {
      const res = await api.login(username, password);
      localStorage.setItem('seal_token', res.token);
      setUser(res.user);
      sealWS.connect(res.token);
    } catch (e: any) {
      setError('Credenciales incorrectas');
      throw e;
    }
  }, []);

  const logout = useCallback(() => {
    localStorage.removeItem('seal_token');
    sealWS.disconnect();
    setUser(null);
  }, []);

  return { user, loading, error, login, logout };
}
