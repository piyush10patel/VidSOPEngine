'use client';

import { useEffect, useState } from 'react';
import { Button, VStack } from '@chakra-ui/react';
import { useParams, useRouter } from 'next/navigation';
import { PageLoading, PageShell } from '@/components/ops/OperationalUI';
import { SOPViewer } from '@/components/SOPViewer';
import { useAuthGuard } from '@/contexts/AuthContext';
import { useI18n } from '@/contexts/I18nContext';
import { api, type SOPResponse } from '@/lib/api';

function roleCanManage(role?: string) {
  return ['admin', 'manager', 'superadmin'].includes((role || 'staff').toLowerCase());
}

export default function SOPDetailPage() {
  const { user, isLoading } = useAuthGuard();
  const { t } = useI18n();
  const router = useRouter();
  const params = useParams();
  const sopId = String(params.id || '');
  const [sop, setSop] = useState<SOPResponse | null>(null);

  useEffect(() => {
    if (!user || !sopId) return;
    api.getManagedSOP(sopId).then(setSop).catch(() => router.push('/sops'));
  }, [router, sopId, user]);

  if (isLoading || !user || !sop) return <PageLoading label={t('sopDetail.openingSop')} />;

  const canManage = roleCanManage(user.role);

  return (
    <PageShell
      eyebrow={t('sopDetail.eyebrow')}
      title={sop.sop?.title || t('sopDetail.untitled')}
      description={t('sopDetail.description')}
      primaryAction={canManage ? <Button className="ops-touch" colorPalette="blue" onClick={() => router.push(`/sops/${sop.id}/edit`)}>{t('sopDetail.editSop')}</Button> : undefined}
      secondaryAction={<Button className="ops-touch" variant="outline" onClick={() => router.push('/sops')}>{t('sopDetail.backToSops')}</Button>}
    >
      <VStack align="stretch" gap="4">
        <SOPViewer
          sop={sop.sop}
          videoId={sop.video_id || undefined}
          editable={false}
          canViewInternal={Boolean(sop.can_view_internal)}
          isFinalized={sop.is_finalized}
        />
      </VStack>
    </PageShell>
  );
}
