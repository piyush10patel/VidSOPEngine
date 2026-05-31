'use client';

import { useEffect, useMemo, useState, type FormEvent } from 'react';
import { useRouter } from 'next/navigation';
import {
  Box,
  Button,
  Container,
  Flex,
  Heading,
  HStack,
  Input,
  Spinner,
  Text,
  VStack,
} from '@chakra-ui/react';
import { LanguageSwitcher } from '@/components/LanguageSwitcher';
import { useAuth } from '@/contexts/AuthContext';
import { useI18n } from '@/contexts/I18nContext';
import { ApiError } from '@/lib/api';

export default function RegisterPage() {
  const { user, isLoading, register } = useAuth();
  const { t } = useI18n();
  const router = useRouter();

  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  useEffect(() => {
    if (!isLoading && user) router.replace('/sops');
  }, [isLoading, router, user]);

  const canSubmit = useMemo(
    () => Boolean(email.trim() && password.length >= 8 && password === confirm),
    [confirm, email, password],
  );

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setError('');
    if (password !== confirm) {
      setError(t('auth.passwordsMismatch'));
      return;
    }
    if (password.length < 8) {
      setError(t('auth.passwordMinimum'));
      return;
    }
    setIsSubmitting(true);
    try {
      await register({ email, password });
      router.push('/login?registered=1');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t('auth.registrationFailed'));
    } finally {
      setIsSubmitting(false);
    }
  };

  if (isLoading) {
    return (
      <Flex minH="100vh" justify="center" align="center">
        <Spinner size="xl" color="blue.500" />
      </Flex>
    );
  }

  return (
    <Flex className="auth-page" minH="100vh" align="center">
      <Container maxW="520px" py={{ base: '5', md: '8' }}>
        <VStack align="stretch" gap="5">
          <Flex justify="space-between" align="center" gap="3" wrap="wrap">
            <Box>
              <Text className="auth-brand">VidSOPEngine</Text>
              <Heading as="h1" mt="2">{t('auth.registerTitle')}</Heading>
            </Box>
            <HStack gap="2">
              <LanguageSwitcher compact />
              <Button variant="ghost" onClick={() => router.push('/')}>{t('common.home')}</Button>
            </HStack>
          </Flex>

          <Box className="auth-form-card">
            <form onSubmit={handleSubmit}>
              <VStack align="stretch" gap="4">
                {error && (
                  <Box className="auth-alert">
                    <Text color="red.700" fontSize="sm">{error}</Text>
                  </Box>
                )}

                <Box>
                  <Text className="auth-label">{t('auth.email')}</Text>
                  <Input
                    className="ops-input"
                    type="email"
                    value={email}
                    onChange={(event) => setEmail(event.target.value)}
                    placeholder="you@example.com"
                    required
                    autoComplete="email"
                  />
                </Box>
                <Box>
                  <Text className="auth-label">{t('auth.password')}</Text>
                  <Input
                    className="ops-input"
                    type="password"
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    placeholder="********"
                    required
                    autoComplete="new-password"
                  />
                </Box>
                <Box>
                  <Text className="auth-label">{t('auth.confirmPassword')}</Text>
                  <Input
                    className="ops-input"
                    type="password"
                    value={confirm}
                    onChange={(event) => setConfirm(event.target.value)}
                    placeholder="********"
                    required
                    autoComplete="new-password"
                  />
                </Box>

                <Button
                  type="submit"
                  colorPalette="blue"
                  size="lg"
                  className="ops-touch"
                  loading={isSubmitting}
                  loadingText={t('auth.creatingWorkspace')}
                  disabled={!canSubmit}
                >
                  {t('auth.createWorkspace')}
                </Button>
              </VStack>
            </form>
          </Box>

          <Flex gap="1" fontSize="sm" color="gray.600" justify="center" wrap="wrap">
            <Text>{t('auth.alreadyAccount')}</Text>
            <Text
              color="blue.600"
              fontWeight="medium"
              cursor="pointer"
              onClick={() => router.push('/login')}
              _hover={{ textDecoration: 'underline' }}
            >
              {t('common.login')}
            </Text>
          </Flex>
        </VStack>
      </Container>
    </Flex>
  );
}
