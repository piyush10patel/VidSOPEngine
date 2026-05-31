'use client';

import { useEffect } from 'react';
import { Box, Button, Flex, Heading, HStack, Text, VStack } from '@chakra-ui/react';
import { useRouter } from 'next/navigation';
import { useAuth } from '@/contexts/AuthContext';
import { useI18n } from '@/contexts/I18nContext';
import { LanguageSwitcher } from '@/components/LanguageSwitcher';

export default function PublicHomePage() {
  const router = useRouter();
  const { user, isLoading } = useAuth();
  const { t } = useI18n();

  useEffect(() => {
    if (!isLoading && user) router.replace('/sops');
  }, [isLoading, user, router]);

  if (isLoading || user) {
    return <Box className="marketing-page" />;
  }

  return (
    <Box className="marketing-page">
      <Box className="marketing-shell" maxW="980px" mx="auto" py={{ base: 8, md: 16 }} px={{ base: 4, md: 8 }}>
        <Flex justify="space-between" align="center" mb={{ base: 8, md: 12 }} gap="3" wrap="wrap">
          <Heading as="h1" size="lg" color="gray.800">VidSOPEngine</Heading>
          <HStack gap="2">
            <LanguageSwitcher compact />
            <Button variant="ghost" size="sm" onClick={() => router.push('/login')}>
              {t('common.login')}
            </Button>
            <Button colorPalette="blue" size="sm" onClick={() => router.push('/register')}>
              {t('common.getStarted')}
            </Button>
          </HStack>
        </Flex>

        <VStack align="start" gap="6" maxW="640px">
          <Heading as="h2" size="2xl" color="gray.900" lineHeight="1.1">
            {t('public.heroTitle')}
          </Heading>
          <Text fontSize="lg" color="gray.600">
            {t('public.heroBody')}
          </Text>
          <HStack gap="3">
            <Button colorPalette="blue" size="lg" onClick={() => router.push('/register')}>
              {t('public.heroPrimary')}
            </Button>
            <Button variant="outline" size="lg" onClick={() => router.push('/login')}>
              {t('common.login')}
            </Button>
          </HStack>
        </VStack>
      </Box>
    </Box>
  );
}
