'use client';

import { useEffect, useState } from 'react';
import { Box, Button, Flex, Text } from '@chakra-ui/react';
import { useI18n } from '@/contexts/I18nContext';

type BeforeInstallPromptEvent = Event & {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed'; platform: string }>;
};

const DISMISS_KEY = 'vidsopengine.pwaInstallDismissed';
// Force the browser to re-check /sw.js this often while the tab is open.
// Browsers do this on navigation anyway, but for a long-running PWA the
// default check (max ~24h) is far too slow for a fast-moving deploy.
const UPDATE_POLL_MS = 60_000;

function canRegisterServiceWorker() {
  if (typeof window === 'undefined') return false;
  return (
    'serviceWorker' in navigator
    && (window.location.protocol === 'https:' || window.location.hostname === 'localhost')
  );
}

export function PWARegister() {
  const { t } = useI18n();
  const [installPrompt, setInstallPrompt] = useState<BeforeInstallPromptEvent | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const [waitingWorker, setWaitingWorker] = useState<ServiceWorker | null>(null);

  useEffect(() => {
    if (!canRegisterServiceWorker()) return;

    let registrationRef: ServiceWorkerRegistration | null = null;

    const triggerUpdateCheck = () => {
      registrationRef?.update().catch(() => undefined);
    };

    navigator.serviceWorker.register('/sw.js').then((registration) => {
      registrationRef = registration;
      // If a new SW finishes installing while the old one is still
      // controlling the page, expose the waiting worker so we can
      // surface a reload prompt to the user.
      const trackWaiting = (worker: ServiceWorker | null) => {
        if (!worker) return;
        if (worker.state === 'installed' && navigator.serviceWorker.controller) {
          setWaitingWorker(worker);
        }
        worker.addEventListener('statechange', () => {
          if (worker.state === 'installed' && navigator.serviceWorker.controller) {
            setWaitingWorker(worker);
          }
        });
      };

      trackWaiting(registration.waiting);
      registration.addEventListener('updatefound', () => {
        trackWaiting(registration.installing);
      });

      // Check once now in case a deploy happened between PWA installs.
      triggerUpdateCheck();
    }).catch(() => undefined);

    // When the new SW takes control, reload so the page uses fresh code.
    let reloading = false;
    const handleControllerChange = () => {
      if (reloading) return;
      reloading = true;
      window.location.reload();
    };
    navigator.serviceWorker.addEventListener('controllerchange', handleControllerChange);

    // Poll for updates while the tab is open + on focus / visibility
    // change. Without this an installed PWA can sit on an old bundle
    // for up to ~24h before the browser checks /sw.js on its own.
    const handleVisibility = () => {
      if (document.visibilityState === 'visible') triggerUpdateCheck();
    };
    const handleFocus = () => triggerUpdateCheck();
    const pollInterval = window.setInterval(triggerUpdateCheck, UPDATE_POLL_MS);
    document.addEventListener('visibilitychange', handleVisibility);
    window.addEventListener('focus', handleFocus);

    const storedDismissed = window.localStorage.getItem(DISMISS_KEY) === '1';
    setDismissed(storedDismissed);

    const handleBeforeInstallPrompt = (event: Event) => {
      event.preventDefault();
      if (!storedDismissed) setInstallPrompt(event as BeforeInstallPromptEvent);
    };

    const handleInstalled = () => {
      setInstallPrompt(null);
      window.localStorage.setItem(DISMISS_KEY, '1');
      setDismissed(true);
    };

    window.addEventListener('beforeinstallprompt', handleBeforeInstallPrompt);
    window.addEventListener('appinstalled', handleInstalled);
    return () => {
      window.removeEventListener('beforeinstallprompt', handleBeforeInstallPrompt);
      window.removeEventListener('appinstalled', handleInstalled);
      navigator.serviceWorker.removeEventListener('controllerchange', handleControllerChange);
      document.removeEventListener('visibilitychange', handleVisibility);
      window.removeEventListener('focus', handleFocus);
      window.clearInterval(pollInterval);
    };
  }, []);

  const applyUpdate = async () => {
    // Happy path: a new SW is waiting. Tell it to activate, the
    // controllerchange listener reloads the page once it takes over.
    if (waitingWorker) {
      waitingWorker.postMessage({ type: 'SKIP_WAITING' });
      return;
    }
    // Fallback: no waiting worker (or the browser never told us about
    // one). Nuke the SW + every cache and hard-reload — this is the
    // sledgehammer that always works.
    try {
      if ('serviceWorker' in navigator) {
        const regs = await navigator.serviceWorker.getRegistrations();
        await Promise.all(regs.map((reg) => reg.unregister()));
      }
      if ('caches' in window) {
        const keys = await caches.keys();
        await Promise.all(keys.map((key) => caches.delete(key)));
      }
    } catch {
      // ignore — we're about to hard-reload regardless
    }
    window.location.reload();
  };

  const install = async () => {
    if (!installPrompt) return;
    await installPrompt.prompt();
    const choice = await installPrompt.userChoice;
    if (choice.outcome === 'accepted') {
      window.localStorage.setItem(DISMISS_KEY, '1');
      setDismissed(true);
    }
    setInstallPrompt(null);
  };

  const dismiss = () => {
    window.localStorage.setItem(DISMISS_KEY, '1');
    setDismissed(true);
    setInstallPrompt(null);
  };

  if (waitingWorker) {
    return (
      <Box position="fixed" left={{ base: '3', md: 'auto' }} right="3" bottom="3" zIndex="1200" maxW={{ base: 'calc(100vw - 24px)', md: '360px' }}>
        <Flex bg="white" border="1px solid" borderColor="blue.200" borderRadius="8px" boxShadow="0 16px 50px rgba(15, 23, 42, 0.18)" p="3" gap="3" align="center">
          <Box flex="1" minW="0">
            <Text fontWeight="800" fontSize="sm" color="gray.900">{t('pwa.updateTitle')}</Text>
            <Text fontSize="xs" color="gray.600">{t('pwa.updateBody')}</Text>
          </Box>
          <Button size="sm" colorPalette="blue" onClick={applyUpdate}>{t('pwa.reload')}</Button>
          <Button size="sm" variant="ghost" onClick={() => setWaitingWorker(null)}>{t('pwa.later')}</Button>
        </Flex>
      </Box>
    );
  }

  if (!installPrompt || dismissed) return null;

  return (
    <Box position="fixed" left={{ base: '3', md: 'auto' }} right="3" bottom="3" zIndex="1200" maxW={{ base: 'calc(100vw - 24px)', md: '360px' }}>
      <Flex bg="white" border="1px solid" borderColor="gray.200" borderRadius="8px" boxShadow="0 16px 50px rgba(15, 23, 42, 0.18)" p="3" gap="3" align="center">
        <Box flex="1" minW="0">
          <Text fontWeight="800" fontSize="sm" color="gray.900">{t('pwa.installTitle')}</Text>
          <Text fontSize="xs" color="gray.600">{t('pwa.installBody')}</Text>
        </Box>
        <Button size="sm" colorPalette="blue" onClick={install}>{t('pwa.install')}</Button>
        <Button size="sm" variant="ghost" onClick={dismiss}>{t('pwa.notNow')}</Button>
      </Flex>
    </Box>
  );
}
