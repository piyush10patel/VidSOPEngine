'use client';

import { Button, HStack, Text } from '@chakra-ui/react';
import { LANGUAGE_REGISTRY, useI18n, type Locale } from '@/contexts/I18nContext';

export function LanguageSwitcher({
  compact = false,
  surface = 'light',
}: {
  compact?: boolean;
  surface?: 'light' | 'dark';
}) {
  const { locale, setLocale, t } = useI18n();
  const tone = surface === 'dark' ? 'whiteAlpha' : 'gray';

  return (
    <HStack gap="1" role="group" aria-label={t('common.language')} wrap="wrap">
      {!compact && (
        <Text fontSize="xs" fontWeight="700" color={surface === 'dark' ? 'whiteAlpha.800' : 'gray.500'}>
          {t('common.language')}
        </Text>
      )}
      {LANGUAGE_REGISTRY.map((entry) => {
        const active = locale === entry.code;
        return (
          <Button
            key={entry.code}
            size="xs"
            variant={active ? 'solid' : 'outline'}
            colorPalette={active ? 'blue' : tone}
            onClick={() => setLocale(entry.code as Locale)}
            title={entry.native}
          >
            {entry.short}
          </Button>
        );
      })}
    </HStack>
  );
}
