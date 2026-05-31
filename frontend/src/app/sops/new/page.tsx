'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Box, Button, Flex, HStack, Text } from '@chakra-ui/react';
import { InlineAlert, OpsPanel, PageLoading, PageShell, SectionHeader } from '@/components/ops/OperationalUI';
import { SOPEditor } from '@/components/SOPEditor';
import { useAuthGuard } from '@/contexts/AuthContext';
import { useI18n } from '@/contexts/I18nContext';

function roleCanManage(role?: string) {
  return ['admin', 'manager', 'superadmin'].includes((role || 'staff').toLowerCase());
}

type Method = 'choose' | 'manual';

export default function NewSOPPage() {
  const { user, isLoading } = useAuthGuard();
  const { t } = useI18n();
  const router = useRouter();
  const [method, setMethod] = useState<Method>('choose');

  useEffect(() => {
    if (user && !roleCanManage(user.role)) router.push('/sops');
  }, [router, user]);

  if (isLoading || !user) return <PageLoading label={t('sopNew.openingEditor')} />;
  if (!roleCanManage(user.role)) {
    return <PageLoading label={t('sopNew.openingLibrary')} />;
  }

  if (method === 'manual') {
    return (
      <PageShell
        eyebrow={t('sopNew.eyebrow')}
        title={t('sopNew.title')}
        description={t('sopNew.description')}
        secondaryAction={<Button className="ops-touch" variant="outline" onClick={() => setMethod('choose')}>{t('sopNew.changeMethod')}</Button>}
      >
        <Box mb="4">
          <InlineAlert tone="blue" title={t('sopNew.voiceTipTitle')}>
            {t('sopNew.voiceTipBody')}
          </InlineAlert>
        </Box>
        <SOPEditor
          onSaved={(sop) => router.push(`/sops/${encodeURIComponent(sop.id)}`)}
          onCancel={() => router.push('/sops')}
        />
      </PageShell>
    );
  }

  return (
    <PageShell
      eyebrow={t('sopNew.eyebrow')}
      title={t('sopNew.chooserTitle')}
      description={t('sopNew.chooserBody')}
      secondaryAction={<Button className="ops-touch" variant="outline" onClick={() => router.push('/sops')}>{t('common.back')}</Button>}
    >
      <Flex direction={{ base: 'column', md: 'row' }} gap="4" align="stretch">
        <OpsPanel className="ops-choice-panel" padded>
          <SectionHeader title={t('sopNew.manualOption')} description={t('sopNew.manualOptionBody')} />
          <HStack mt="2">
            <Button className="ops-touch" colorPalette="blue" onClick={() => setMethod('manual')}>
              {t('sopNew.startManual')}
            </Button>
          </HStack>
          <Box mt="3">
            <Text fontSize="xs" color="gray.600" fontWeight="700">{t('sopNew.voiceTipTitle')}</Text>
            <Text fontSize="xs" color="gray.500">{t('sopNew.voiceTipBody')}</Text>
          </Box>
        </OpsPanel>
        <OpsPanel className="ops-choice-panel" padded>
          <SectionHeader title={t('sopNew.videoOption')} description={t('sopNew.videoOptionBody')} />
          <HStack mt="2">
            <Button className="ops-touch" variant="outline" colorPalette="blue" onClick={() => router.push('/upload')}>
              {t('sopNew.startVideo')}
            </Button>
          </HStack>
        </OpsPanel>
      </Flex>
    </PageShell>
  );
}
