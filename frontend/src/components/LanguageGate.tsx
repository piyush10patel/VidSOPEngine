'use client';

import { Box, Button, Flex, Heading, Text, VStack } from '@chakra-ui/react';
import { LANGUAGE_REGISTRY, useI18n, type Locale } from '@/contexts/I18nContext';

export function LanguageGate() {
  const { hasChosenLanguage, isReady, locale, setLocale, t } = useI18n();

  if (!isReady || hasChosenLanguage) return null;

  return (
    <Flex
      position="fixed"
      inset="0"
      zIndex="1600"
      bg="rgba(15, 23, 42, 0.58)"
      align="center"
      justify="center"
      p="4"
    >
      <Box
        w="full"
        maxW="520px"
        bg="white"
        border="1px solid"
        borderColor="gray.200"
        borderRadius="12px"
        p={{ base: '5', md: '6' }}
        boxShadow="0 24px 80px rgba(15, 23, 42, 0.22)"
      >
        <VStack align="stretch" gap="4">
          <Box>
            <Text fontSize="xs" fontWeight="800" color="blue.600" textTransform="uppercase">
              VidSOPEngine
            </Text>
            <Heading as="h2" size="lg" mt="1">
              {t('languageGate.title')}
            </Heading>
            <Text color="gray.600" mt="2">
              {t('languageGate.body')}
            </Text>
          </Box>
          <Flex gap="3" direction={{ base: 'column', sm: 'row' }} wrap="wrap">
            {LANGUAGE_REGISTRY.map((entry) => (
              <Button
                key={entry.code}
                className="ops-touch"
                flex="1"
                variant={locale === entry.code ? 'solid' : 'outline'}
                colorPalette="blue"
                onClick={() => setLocale(entry.code as Locale)}
              >
                {entry.native}
              </Button>
            ))}
          </Flex>
        </VStack>
      </Box>
    </Flex>
  );
}
