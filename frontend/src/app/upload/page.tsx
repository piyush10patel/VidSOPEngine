'use client';

import { useRouter } from 'next/navigation';
import { Box, Button, Text, VStack } from '@chakra-ui/react';
import { Sparkles } from 'lucide-react';
import { UploadForm } from '@/components/UploadForm';
import {
  InlineAlert,
  OpsPanel,
  PageLoading,
  PageShell,
} from '@/components/ops/OperationalUI';
import { type Video } from '@/lib/api';
import { useAuthGuard } from '@/contexts/AuthContext';
import { useI18n } from '@/contexts/I18nContext';

export default function UploadPage() {
  const { user, isLoading } = useAuthGuard();
  const router = useRouter();
  const { t } = useI18n();

  if (isLoading || !user) return <PageLoading label={t('upload.openingSetup')} />;

  const role = (user.role || 'staff').toLowerCase();
  const canCreate = role === 'admin' || role === 'manager' || role === 'superadmin';

  if (!canCreate) {
    return (
      <PageShell
        eyebrow={t('common.required')}
        title={t('upload.restrictedTitle')}
        description={t('upload.restrictedDescription')}
        secondaryAction={<Button className="ops-touch" variant="outline" onClick={() => router.push('/sops')}>{t('nav.sops')}</Button>}
      >
        <InlineAlert tone="neutral" title={t('upload.accessLimited')}>{t('upload.accessLimitedBody')}</InlineAlert>
      </PageShell>
    );
  }

  const handleUploadComplete = (video: Video) => {
    router.push(`/videos/${video.id}?auto=1`);
  };

  return (
    <PageShell
      eyebrow={t('upload.eyebrow')}
      title={t('upload.sopAiTitle')}
      description={t('upload.sopAiDescription')}
      secondaryAction={<Button className="ops-touch" variant="outline" onClick={() => router.push('/sops')}>{t('upload.backToSops')}</Button>}
      maxW="900px"
    >
      <VStack align="stretch" gap="4">
        <Box className="sop-ai-hero">
          <Box className="sop-ai-hero-glow" aria-hidden="true" />
          <Box className="sop-ai-hero-content">
            <Box className="sop-ai-hero-badge">
              <Sparkles size={14} /> {t('upload.aiBadge')}
            </Box>
            <Text className="sop-ai-hero-title">{t('upload.heroTitle')}</Text>
            <Text className="sop-ai-hero-sub">{t('upload.heroBody')}</Text>
          </Box>
        </Box>

        <OpsPanel>
          <UploadForm onUploadComplete={handleUploadComplete} />
        </OpsPanel>
      </VStack>
    </PageShell>
  );
}
