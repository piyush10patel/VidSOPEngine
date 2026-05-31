'use client';

import { createContext, useContext, useEffect, useMemo, useState } from 'react';
import en from '../../messages/en.json';
import hi from '../../messages/hi.json';
import mr from '../../messages/mr.json';

// Language registry — single source of truth on the frontend. Mirrors
// app/core/languages.py on the backend. Adding a new language is:
//   1. drop a new messages/<code>.json bundle (partial is fine — every
//      missing key falls back to English via the t() resolver),
//   2. import it at the top of this file,
//   3. add an entry below.
// Nothing else in the codebase needs to change — LanguageSwitcher reads
// from this registry and renders dynamically.
export const LANGUAGE_REGISTRY = [
  { code: 'en', native: 'English', short: 'EN', bundle: en },
  { code: 'hi', native: 'हिन्दी', short: 'हिं', bundle: hi },
  { code: 'mr', native: 'मराठी', short: 'मरा', bundle: mr },
] as const;

export type Locale = (typeof LANGUAGE_REGISTRY)[number]['code'];

type Messages = Record<string, unknown>;

interface I18nContextValue {
  locale: Locale;
  isReady: boolean;
  hasChosenLanguage: boolean;
  t: (key: string) => string;
  setLocale: (locale: Locale, markChosen?: boolean) => void;
}

const STORAGE_KEY = 'vidsopengine.language';
const SELECTED_KEY = 'vidsopengine.languageSelected';

const bundles: Record<Locale, Messages> = LANGUAGE_REGISTRY.reduce((acc, item) => {
  (acc as Record<string, Messages>)[item.code] = item.bundle as Messages;
  return acc;
}, {} as Record<Locale, Messages>);

const VALID_CODES = new Set<string>(LANGUAGE_REGISTRY.map((item) => item.code));

function isLocale(value: string): value is Locale {
  return VALID_CODES.has(value);
}

const I18nContext = createContext<I18nContextValue | null>(null);

function readMessage(bundle: Messages, key: string): string | undefined {
  const value = key.split('.').reduce<unknown>((cursor, part) => {
    if (!cursor || typeof cursor !== 'object') return undefined;
    return (cursor as Record<string, unknown>)[part];
  }, bundle);
  return typeof value === 'string' ? value : undefined;
}

export function I18nProvider({ children }: { children: React.ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>('en');
  const [isReady, setIsReady] = useState(false);
  const [hasChosenLanguage, setHasChosenLanguage] = useState(false);

  useEffect(() => {
    const stored = typeof window !== 'undefined' ? window.localStorage.getItem(STORAGE_KEY) : null;
    const selected = typeof window !== 'undefined' ? window.localStorage.getItem(SELECTED_KEY) === 'true' : false;
    if (stored && isLocale(stored)) {
      setLocaleState(stored);
    }
    setHasChosenLanguage(selected);
    setIsReady(true);
  }, []);

  useEffect(() => {
    if (!isReady) return;
    // Pass the canonical 2-letter code to <html lang>; Indian language
    // codes ("hi", "mr") are the right shape for screen readers and
    // search-engine hints.
    document.documentElement.lang = locale;
  }, [isReady, locale]);

  const setLocale = (next: Locale, markChosen = true) => {
    setLocaleState(next);
    if (typeof window !== 'undefined') {
      window.localStorage.setItem(STORAGE_KEY, next);
      if (markChosen) window.localStorage.setItem(SELECTED_KEY, 'true');
    }
    if (markChosen) setHasChosenLanguage(true);
  };

  const value = useMemo<I18nContextValue>(() => ({
    locale,
    isReady,
    hasChosenLanguage,
    setLocale,
    // Fallback chain: chosen locale → English. Keeps partial translations
    // viable — a brand-new language bundle can start with just nav.* and
    // common.* and grow over time.
    t: (key: string) => readMessage(bundles[locale], key) || readMessage(en as Messages, key) || key,
  }), [hasChosenLanguage, isReady, locale]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n() {
  const context = useContext(I18nContext);
  if (!context) throw new Error('useI18n must be used inside I18nProvider');
  return context;
}
