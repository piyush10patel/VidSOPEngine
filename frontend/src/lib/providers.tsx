'use client';

import { ChakraProvider, defaultSystem } from '@chakra-ui/react';
import { AuthProvider } from '@/contexts/AuthContext';
import { I18nProvider } from '@/contexts/I18nContext';
import { Navbar } from '@/components/Navbar';
import { PWARegister } from '@/components/PWARegister';
import { LanguageGate } from '@/components/LanguageGate';

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <ChakraProvider value={defaultSystem}>
      <I18nProvider>
        <AuthProvider>
          <Navbar />
          <main className="app-main">{children}</main>
          <LanguageGate />
          <PWARegister />
        </AuthProvider>
      </I18nProvider>
    </ChakraProvider>
  );
}
