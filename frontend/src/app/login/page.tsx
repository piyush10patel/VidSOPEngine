'use client';

import { useEffect, useState, type FormEvent } from 'react';
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
import { ArrowLeft, Check } from 'lucide-react';
import { LanguageSwitcher } from '@/components/LanguageSwitcher';
import { useAuth } from '@/contexts/AuthContext';
import { useI18n } from '@/contexts/I18nContext';
import { ApiError, api } from '@/lib/api';

type Mode =
  | { kind: 'login' }
  | { kind: 'forgot' }
  | { kind: 'verify'; email: string; devOtp?: string }
  | { kind: 'reset'; email: string; token: string };

export default function LoginPage() {
  const { user, isLoading, login } = useAuth();
  const { t } = useI18n();
  const router = useRouter();

  const [mode, setMode] = useState<Mode>({ kind: 'login' });
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [info, setInfo] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Reset-flow fields (kept separate so switching modes doesn't bleed state)
  const [resetEmail, setResetEmail] = useState('');
  const [otpCode, setOtpCode] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');

  useEffect(() => {
    if (!isLoading && user) router.replace('/sops');
  }, [isLoading, router, user]);

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    setError('');
    setIsSubmitting(true);
    try {
      await login(email, password);
      router.replace('/sops');
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t('auth.loginFailed'));
    } finally {
      setIsSubmitting(false);
    }
  };

  const requestOtp = async (event: FormEvent) => {
    event.preventDefault();
    setError('');
    setInfo('');
    setIsSubmitting(true);
    try {
      const res = await api.forgotPassword(resetEmail.trim());
      setInfo(res.message);
      // dev_otp is only returned when SMTP is unconfigured AND the
      // backend has auth_expose_otp_in_dev=True. It pre-fills the
      // code field so the QA flow doesn't need a real inbox.
      setMode({ kind: 'verify', email: resetEmail.trim(), devOtp: res.dev_otp });
      if (res.dev_otp) setOtpCode(res.dev_otp);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t('auth.forgotFailed'));
    } finally {
      setIsSubmitting(false);
    }
  };

  const verifyOtp = async (event: FormEvent) => {
    event.preventDefault();
    if (mode.kind !== 'verify') return;
    setError('');
    setInfo('');
    setIsSubmitting(true);
    try {
      const res = await api.verifyOtp(mode.email, otpCode.trim());
      setMode({ kind: 'reset', email: mode.email, token: res.reset_token });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t('auth.otpFailed'));
    } finally {
      setIsSubmitting(false);
    }
  };

  const submitReset = async (event: FormEvent) => {
    event.preventDefault();
    if (mode.kind !== 'reset') return;
    if (newPassword !== confirmPassword) {
      setError(t('auth.passwordMismatch'));
      return;
    }
    if (newPassword.length < 8) {
      setError(t('auth.passwordTooShort'));
      return;
    }
    setError('');
    setIsSubmitting(true);
    try {
      await api.resetPassword(mode.token, newPassword);
      // Auto sign-in with the new password so the user lands in their
      // workspace without typing it again. If login fails for any
      // reason (rare), fall back to the success message + manual login.
      try {
        await login(mode.email, newPassword);
        router.replace('/sops');
        return;
      } catch {
        // fall through
      }
      setMode({ kind: 'login' });
      setEmail(mode.email);
      setPassword('');
      setNewPassword('');
      setConfirmPassword('');
      setOtpCode('');
      setInfo(t('auth.passwordChangedSignIn'));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t('auth.resetFailed'));
    } finally {
      setIsSubmitting(false);
    }
  };

  const switchTo = (next: Mode) => {
    setError('');
    setInfo('');
    setMode(next);
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
      <Container maxW="1180px" py={{ base: '5', md: '8' }}>
        <Flex className="auth-layout" gap={{ base: '5', lg: '8' }} direction={{ base: 'column', lg: 'row' }}>
          <Box className="auth-story">
            <Flex justify="space-between" align="center" gap="3">
              <Box>
                <Text className="auth-brand">VidSOPEngine</Text>
                <Text className="auth-tagline">{t('common.tagline')}</Text>
              </Box>
              <LanguageSwitcher compact />
            </Flex>

            <Box className="auth-story-copy">
              <Text className="marketing-eyebrow">{t('common.tagline')}</Text>
              <Heading as="h1">{t('auth.loginTitle')}</Heading>
              <Text>{t('auth.loginBody')}</Text>
              <Box className="auth-positioning">{t('auth.loginPositioning')}</Box>
            </Box>

            <VStack align="stretch" gap="3">
              {[t('landing.stat1'), t('landing.stat2'), t('landing.stat3')].map((item) => (
                <Flex key={item} className="auth-proof-row" gap="3" align="center">
                  <Box className="auth-proof-mark" />
                  <Text>{item}</Text>
                </Flex>
              ))}
            </VStack>
          </Box>

          <Box className="auth-form-card">
            <VStack align="stretch" gap="6">
              <HStack justify="space-between" align="start">
                <Box>
                  <Text className="marketing-eyebrow">
                    {mode.kind === 'login' ? t('common.login')
                      : mode.kind === 'forgot' ? t('auth.forgotEyebrow')
                      : mode.kind === 'verify' ? t('auth.otpEyebrow')
                      : t('auth.resetEyebrow')}
                  </Text>
                  <Heading as="h2" size="lg" mt="1">
                    {mode.kind === 'login' ? t('auth.loginTitle')
                      : mode.kind === 'forgot' ? t('auth.forgotTitle')
                      : mode.kind === 'verify' ? t('auth.otpTitle')
                      : t('auth.resetTitle')}
                  </Heading>
                </Box>
                <Button variant="ghost" size="sm" onClick={() => router.push('/')}>
                  {t('common.home')}
                </Button>
              </HStack>

              {error && (
                <Box className="auth-alert">
                  <Text color="red.700" fontSize="sm">{error}</Text>
                </Box>
              )}
              {info && (
                <Box className="auth-info">
                  <Text color="blue.700" fontSize="sm">{info}</Text>
                </Box>
              )}

              {mode.kind === 'login' && (
                <form onSubmit={handleSubmit}>
                  <VStack gap="5" align="stretch">
                    <Box>
                      <Text className="auth-label">{t('auth.email')}</Text>
                      <Input
                        className="ops-input"
                        type="email"
                        value={email}
                        onChange={(event) => setEmail(event.target.value)}
                        placeholder="you@company.com"
                        required
                        autoComplete="email"
                      />
                    </Box>
                    <Box>
                      <Flex justify="space-between" align="center" mb="1">
                        <Text className="auth-label" mb="0">{t('auth.password')}</Text>
                        <Text
                          fontSize="xs"
                          color="blue.600"
                          fontWeight="700"
                          cursor="pointer"
                          onClick={() => {
                            setResetEmail(email);
                            switchTo({ kind: 'forgot' });
                          }}
                          _hover={{ textDecoration: 'underline' }}
                        >
                          {t('auth.forgotLink')}
                        </Text>
                      </Flex>
                      <Input
                        className="ops-input"
                        type="password"
                        value={password}
                        onChange={(event) => setPassword(event.target.value)}
                        placeholder="********"
                        required
                        autoComplete="current-password"
                      />
                    </Box>
                    <Button
                      type="submit"
                      colorPalette="blue"
                      className="ops-touch"
                      w="full"
                      loading={isSubmitting}
                      loadingText={t('auth.signingIn')}
                    >
                      {t('common.login')}
                    </Button>
                  </VStack>
                </form>
              )}

              {mode.kind === 'forgot' && (
                <form onSubmit={requestOtp}>
                  <VStack gap="5" align="stretch">
                    <Text fontSize="sm" color="gray.600">{t('auth.forgotBody')}</Text>
                    <Box>
                      <Text className="auth-label">{t('auth.email')}</Text>
                      <Input
                        className="ops-input"
                        type="email"
                        value={resetEmail}
                        onChange={(event) => setResetEmail(event.target.value)}
                        placeholder="you@company.com"
                        required
                        autoComplete="email"
                      />
                    </Box>
                    <Button
                      type="submit"
                      colorPalette="blue"
                      className="ops-touch"
                      w="full"
                      loading={isSubmitting}
                      loadingText={t('auth.sending')}
                    >
                      {t('auth.sendCode')}
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      onClick={() => switchTo({ kind: 'login' })}
                    >
                      <ArrowLeft size={14} /> {t('auth.backToLogin')}
                    </Button>
                  </VStack>
                </form>
              )}

              {mode.kind === 'verify' && (
                <form onSubmit={verifyOtp}>
                  <VStack gap="5" align="stretch">
                    <Text fontSize="sm" color="gray.600">
                      {t('auth.otpBody').replace('{email}', mode.email)}
                    </Text>
                    {mode.devOtp && (
                      <Box className="auth-info">
                        <Text fontSize="xs" color="blue.700">
                          {t('auth.devOtpHint').replace('{code}', mode.devOtp)}
                        </Text>
                      </Box>
                    )}
                    <Box>
                      <Text className="auth-label">{t('auth.otpLabel')}</Text>
                      <Input
                        className="ops-input"
                        inputMode="numeric"
                        pattern="[0-9]*"
                        autoComplete="one-time-code"
                        value={otpCode}
                        onChange={(event) => setOtpCode(event.target.value.replace(/[^0-9]/g, ''))}
                        placeholder="000000"
                        maxLength={6}
                        required
                      />
                    </Box>
                    <Button
                      type="submit"
                      colorPalette="blue"
                      className="ops-touch"
                      w="full"
                      loading={isSubmitting}
                      loadingText={t('auth.verifying')}
                    >
                      {t('auth.verifyCode')}
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      onClick={() => switchTo({ kind: 'forgot' })}
                    >
                      <ArrowLeft size={14} /> {t('auth.useDifferentEmail')}
                    </Button>
                  </VStack>
                </form>
              )}

              {mode.kind === 'reset' && (
                <form onSubmit={submitReset}>
                  <VStack gap="5" align="stretch">
                    <Text fontSize="sm" color="gray.600">{t('auth.resetBody')}</Text>
                    <Box>
                      <Text className="auth-label">{t('auth.newPassword')}</Text>
                      <Input
                        className="ops-input"
                        type="password"
                        value={newPassword}
                        onChange={(event) => setNewPassword(event.target.value)}
                        placeholder={t('auth.newPasswordPlaceholder')}
                        required
                        autoComplete="new-password"
                        minLength={8}
                      />
                    </Box>
                    <Box>
                      <Text className="auth-label">{t('auth.confirmPassword')}</Text>
                      <Input
                        className="ops-input"
                        type="password"
                        value={confirmPassword}
                        onChange={(event) => setConfirmPassword(event.target.value)}
                        placeholder={t('auth.confirmPasswordPlaceholder')}
                        required
                        autoComplete="new-password"
                        minLength={8}
                      />
                    </Box>
                    <Button
                      type="submit"
                      colorPalette="blue"
                      className="ops-touch"
                      w="full"
                      loading={isSubmitting}
                      loadingText={t('auth.updating')}
                    >
                      <Check size={14} /> {t('auth.changePassword')}
                    </Button>
                  </VStack>
                </form>
              )}

              {mode.kind === 'login' && (
                <Flex gap="1" fontSize="sm" color="gray.600" wrap="wrap">
                  <Text>{t('auth.noAccount')}</Text>
                  <Text
                    color="blue.600"
                    fontWeight="medium"
                    cursor="pointer"
                    onClick={() => router.push('/register')}
                    _hover={{ textDecoration: 'underline' }}
                  >
                    {t('auth.createOne')}
                  </Text>
                </Flex>
              )}
            </VStack>
          </Box>
        </Flex>
      </Container>
    </Flex>
  );
}
