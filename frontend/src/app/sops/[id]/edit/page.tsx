'use client';

import { useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { PageLoading, PageShell } from '@/components/ops/OperationalUI';
import { SOPEditor } from '@/components/SOPEditor';
import { useAuthGuard } from '@/contexts/AuthContext';
import { useI18n } from '@/contexts/I18nContext';
import { api, type SOPResponse } from '@/lib/api';

function roleCanManage(role?: string) {
  return ['admin', 'manager', 'superadmin'].includes((role || 'staff').toLowerCase());
}

export default function EditSOPPage() {
  const { user, isLoading } = useAuthGuard();
  const { t } = useI18n();
  const router = useRouter();
  const params = useParams();
  const sopId = String(params.id || '');
  const [sop, setSop] = useState<SOPResponse | null>(null);

  useEffect(() => {
    if (!user || !sopId) return;
    if (!roleCanManage(user.role)) {
      router.push(`/sops/${encodeURIComponent(sopId)}`);
      return;
    }
    api.getManagedSOP(sopId).then(setSop).catch(() => router.push('/sops'));
  }, [router, sopId, user]);

  if (isLoading || !user || !sop) return <PageLoading label={t('sopEdit.openingEditor')} />;

  return (
    <PageShell eyebrow={t('sopEdit.eyebrow')} title={t('sopEdit.title')} description={t('sopEdit.description')}>
      <SOPEditor initial={sop} onSaved={() => router.push('/sops')} onCancel={() => router.push('/sops')} />
    </PageShell>
  );
}
