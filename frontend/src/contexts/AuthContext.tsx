'use client';

import React, { createContext, useContext, useEffect, useState, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import api, { type RegisterPayload, type User } from '@/lib/api';

interface AuthContextType {
  user: User | null;
  isLoading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (payload: RegisterPayload) => Promise<User>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextType | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    async function restoreSession() {
      const token = typeof window !== 'undefined' ? localStorage.getItem('token') : null;
      try {
        if (token) {
          try {
            const current = await api.getCurrentUser();
            if (!cancelled) setUser(current);
            return;
          } catch {
            localStorage.removeItem('token');
          }
        }
        const refreshed = await api.refreshSession();
        if (!cancelled) {
          setUser(refreshed.user || await api.getCurrentUser());
        }
      } catch {
        if (typeof window !== 'undefined') {
          localStorage.removeItem('token');
        }
        if (!cancelled) setUser(null);
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    }
    restoreSession();
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const response = await api.login(email, password);
    setUser(response.user || await api.getCurrentUser());
  }, []);

  const register = useCallback(async (payload: RegisterPayload): Promise<User> => {
    return api.register(payload);
  }, []);

  const logout = useCallback(() => {
    void api.logoutSession();
    api.logout();
    setUser(null);
  }, []);

  return (
    <AuthContext.Provider value={{ user, isLoading, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthContextType {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}

export function useAuthGuard() {
  const { user, isLoading } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (!isLoading && !user) {
      router.replace('/login');
    }
  }, [user, isLoading, router]);

  return { user, isLoading };
}
