'use client';

import { useEffect, useState } from 'react';
import { Box, Button, Heading, Text, VStack } from '@chakra-ui/react';

// Emergency escape hatch for stuck PWA caches.
//
// If a user reports "the reload button does nothing" or "the app shows
// old content after deploy", point them to /reset. This unregisters
// every service worker we own, drops every Cache Storage entry, and
// hard-reloads to the homepage. It runs without any auth dependency
// so it works even if the cached bundle is broken enough to crash the
// rest of the app.
export default function ResetPage() {
  const [status, setStatus] = useState<'working' | 'done' | 'error'>('working');

  useEffect(() => {
    let cancelled = false;
    const nuke = async () => {
      try {
        if ('serviceWorker' in navigator) {
          const registrations = await navigator.serviceWorker.getRegistrations();
          await Promise.all(registrations.map((reg) => reg.unregister()));
        }
        if ('caches' in window) {
          const keys = await caches.keys();
          await Promise.all(keys.map((key) => caches.delete(key)));
        }
        if (cancelled) return;
        setStatus('done');
        // Give the user a beat to see the success message, then
        // bounce them to /, bypassing the bf-cache.
        window.setTimeout(() => {
          window.location.replace('/');
        }, 1200);
      } catch {
        if (!cancelled) setStatus('error');
      }
    };
    void nuke();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <Box minH="100vh" display="flex" alignItems="center" justifyContent="center" p="6" bg="gray.50">
      <VStack gap="4" maxW="420px" textAlign="center">
        <Heading size="md" color="gray.800">Clearing app cache…</Heading>
        {status === 'working' && (
          <Text fontSize="sm" color="gray.600">
            Unregistering the service worker and clearing every cached file. This usually takes under a second.
          </Text>
        )}
        {status === 'done' && (
          <Text fontSize="sm" color="green.700">
            Done. Reloading with the latest version…
          </Text>
        )}
        {status === 'error' && (
          <>
            <Text fontSize="sm" color="red.700">
              Something blocked the automatic cleanup. Try one of these:
            </Text>
            <VStack gap="1" align="start" fontSize="sm" color="gray.700">
              <Text>- Open DevTools (F12) → Application → Service Workers → Unregister</Text>
              <Text>- Then DevTools → Application → Storage → Clear site data</Text>
              <Text>- Then reload the page</Text>
            </VStack>
            <Button colorPalette="blue" onClick={() => window.location.replace('/')}>
              Try going home anyway
            </Button>
          </>
        )}
      </VStack>
    </Box>
  );
}
