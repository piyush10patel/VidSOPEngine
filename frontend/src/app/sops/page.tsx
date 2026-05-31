'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Box, Button, Flex, HStack, Input, Text, VStack } from '@chakra-ui/react';
import {
  Archive,
  ArchiveRestore,
  BookOpen,
  ExternalLink,
  Folder,
  FolderInput,
  FolderOpen,
  Pencil,
  Plus,
  RefreshCw,
} from 'lucide-react';
import { useAuthGuard } from '@/contexts/AuthContext';
import { useI18n } from '@/contexts/I18nContext';
import { EmptyAction, InlineAlert, OpsPanel, PageLoading, PageShell, QuickActionCard, SectionHeader, StatusPill } from '@/components/ops/OperationalUI';
import { formatShortDate } from '@/lib/design';
import { api, type SOPFolder, type SOPResponse } from '@/lib/api';

const UNFILED = '__unfiled';

function roleCanManage(role?: string) {
  return ['admin', 'manager', 'superadmin'].includes((role || 'staff').toLowerCase());
}

function sopTitle(sop: SOPResponse, fallback: string) {
  return sop.sop?.title || fallback;
}

function folderName(foldersById: Map<string, SOPFolder>, folderId: string | null | undefined, none: string, unknown: string) {
  if (!folderId) return none;
  return foldersById.get(folderId)?.name || unknown;
}

function SOPRow({
  sop,
  foldersById,
  canManage,
  onOpen,
  onEdit,
  onArchive,
  onUnarchive,
}: {
  sop: SOPResponse;
  foldersById: Map<string, SOPFolder>;
  canManage: boolean;
  onOpen: () => void;
  onEdit: () => void;
  onArchive: () => void;
  onUnarchive: () => void;
}) {
  const { t } = useI18n();
  const stepCount = sop.sop?.steps?.length || 0;
  const updated = sop.updated_at || sop.created_at;
  const folderLabel = folderName(foldersById, sop.folder_id, t('sopLibrary.noFolder'), t('sopLibrary.unknownFolder'));
  const iconTone = sop.archived ? 'neutral' : sop.is_finalized ? 'green' : 'blue';

  return (
    <Box className="sop-card-v2" onClick={onOpen} role="button" tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onOpen();
        }
      }}>
      <Flex className="sop-card-v2-head" gap="3" align="flex-start">
        <Box className={`ops-row-icon ops-row-icon-${iconTone}`}>
          {sop.archived ? <Archive size={16} /> : <BookOpen size={16} />}
        </Box>
        <Box flex="1" minW="0">
          <Text className="sop-card-v2-title sop-text-wrap">
            {sopTitle(sop, t('sopLibrary.untitledSop'))}
          </Text>
          <HStack gap="1" wrap="wrap" mt="1">
            {sop.is_finalized && <StatusPill tone="green">{t('sopLibrary.finalPill')}</StatusPill>}
            {sop.archived && <StatusPill tone="neutral">{t('sopLibrary.archivedPill')}</StatusPill>}
            <StatusPill tone="blue">{sop.category || t('sopLibrary.uncategorized')}</StatusPill>
          </HStack>
        </Box>
      </Flex>

      <Text className="sop-card-v2-desc sop-text-wrap">
        {sop.sop?.description || t('sopLibrary.operationalProcedure')}
      </Text>

      <Box className="sop-card-v2-meta">
        <span><strong>{stepCount}</strong> {t('sopLibrary.stepsLabel')}</span>
        <span className="sop-card-v2-meta-dot">·</span>
        <span>{folderLabel}</span>
        <span className="sop-card-v2-meta-dot">·</span>
        <span>{t('sopLibrary.updatedSuffix').replace('{date}', formatShortDate(updated))}</span>
      </Box>

      {(sop.linked_workflows_count || sop.linked_checklists_count || sop.linked_training_count) ? (
        <HStack gap="1" wrap="wrap" mt="2">
          {!!(sop.linked_workflows_count || 0) && (
            <StatusPill tone="green">
              {t('sopLibrary.workflowsCount').replace('{count}', String(sop.linked_workflows_count || 0))}
            </StatusPill>
          )}
          {!!(sop.linked_checklists_count || 0) && (
            <StatusPill tone="green">
              {t('sopLibrary.checklistsCount').replace('{count}', String(sop.linked_checklists_count || 0))}
            </StatusPill>
          )}
          {!!(sop.linked_training_count || 0) && (
            <StatusPill tone="green">
              {t('sopLibrary.trainingCount').replace('{count}', String(sop.linked_training_count || 0))}
            </StatusPill>
          )}
        </HStack>
      ) : null}

      <Flex
        className="sop-card-v2-actions"
        gap="1"
        justify="flex-end"
        onClick={(e) => e.stopPropagation()}
      >
        <Button
          size="sm"
          colorPalette="blue"
          onClick={(e) => { e.stopPropagation(); onOpen(); }}
          aria-label={t('sopLibrary.open')}
        >
          <ExternalLink size={14} /> {t('sopLibrary.open')}
        </Button>
        {canManage && (
          <Button
            size="sm"
            variant="outline"
            onClick={(e) => { e.stopPropagation(); onEdit(); }}
            aria-label={t('sopLibrary.edit')}
          >
            <Pencil size={14} /> {t('sopLibrary.edit')}
          </Button>
        )}
        {canManage && (
          sop.archived ? (
            <Button
              size="sm"
              variant="ghost"
              onClick={(e) => { e.stopPropagation(); onUnarchive(); }}
              aria-label={t('sopLibrary.unarchive')}
            >
              <ArchiveRestore size={14} />
            </Button>
          ) : (
            <Button
              size="sm"
              variant="ghost"
              colorPalette="red"
              onClick={(e) => { e.stopPropagation(); onArchive(); }}
              aria-label={t('sopLibrary.archive')}
            >
              <Archive size={14} />
            </Button>
          )
        )}
      </Flex>
    </Box>
  );
}

export default function SOPManagementPage() {
  const { user, isLoading: authLoading } = useAuthGuard();
  const { t } = useI18n();
  const router = useRouter();
  const canManage = roleCanManage(user?.role);
  const [sops, setSops] = useState<SOPResponse[]>([]);
  const [folders, setFolders] = useState<SOPFolder[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [selectedFolder, setSelectedFolder] = useState('');
  const [category, setCategory] = useState('');
  const [showArchived, setShowArchived] = useState(false);
  const [newFolderName, setNewFolderName] = useState('');

  const foldersById = useMemo(
    () => new Map(folders.map((folder) => [folder.id, folder])),
    [folders],
  );

  const load = useCallback(async () => {
    setIsLoading(true);
    try {
      const [sopData, folderData] = await Promise.all([
        api.listManagedSOPs({
          search: search || undefined,
          category: category || undefined,
          archived: showArchived,
        }),
        api.listSOPFolders(),
      ]);
      setSops(sopData.sops);
      setFolders(folderData.folders);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : t('sopLibrary.loadFailedDetail'));
    } finally {
      setIsLoading(false);
    }
  }, [category, search, showArchived, t]);

  useEffect(() => {
    if (user) void load();
  }, [load, user]);

  const visibleSOPs = useMemo(() => {
    return sops.filter((sop) => {
      if (selectedFolder === UNFILED) return !sop.folder_id;
      if (selectedFolder) return sop.folder_id === selectedFolder;
      return true;
    });
  }, [selectedFolder, sops]);

  const categories = useMemo(
    () => Array.from(new Set(sops.map((sop) => sop.category || t('sopLibrary.uncategorized')))).sort(),
    [sops, t],
  );

  const createFolder = async () => {
    const name = newFolderName.trim();
    if (!name) return;
    await api.createSOPFolder(name);
    setNewFolderName('');
    await load();
  };

  if (authLoading || !user) return <PageLoading label={t('sopLibrary.openingLibrary')} />;

  return (
    <PageShell
      eyebrow={t('sopLibrary.eyebrow')}
      title={t('sopLibrary.title')}
      description={t('sopLibrary.description')}
    >
      <Box display="grid" gridTemplateColumns={canManage ? 'repeat(2, minmax(0, 1fr))' : '1fr'} gap="3" mb="4">
        {canManage && (
          <QuickActionCard
            icon={Plus}
            tone="green"
            label={t('sopLibrary.createSop')}
            onClick={() => router.push('/sops/new')}
          />
        )}
        <QuickActionCard
          icon={RefreshCw}
          tone="blue"
          label={t('sopLibrary.refresh')}
          onClick={load}
        />
      </Box>

      {error && (
        <Box mb="4">
          <InlineAlert tone="red" title={t('sopLibrary.loadFailed')}>{error}</InlineAlert>
        </Box>
      )}

      <Flex direction={{ base: 'column', lg: 'row' }} gap="4" align="start">
        <OpsPanel className="ops-sidebar-panel">
          <VStack align="stretch" gap="2">
            <SectionHeader title={t('sopLibrary.foldersTitle')} description={t('sopLibrary.foldersCount').replace('{count}', String(folders.length))} />
            <Button className="ops-touch" variant={!selectedFolder ? 'subtle' : 'ghost'} justifyContent="flex-start" onClick={() => setSelectedFolder('')}>
              <FolderOpen size={16} /> {t('sopLibrary.allSops')}
            </Button>
            <Button className="ops-touch" variant={selectedFolder === UNFILED ? 'subtle' : 'ghost'} justifyContent="flex-start" onClick={() => setSelectedFolder(UNFILED)}>
              <FolderInput size={16} /> {t('sopLibrary.noFolder')}
            </Button>
            {folders.map((folder) => (
              <Button
                key={folder.id}
                className="ops-touch"
                variant={selectedFolder === folder.id ? 'subtle' : 'ghost'}
                justifyContent="flex-start"
                onClick={() => setSelectedFolder(folder.id)}
              >
                <Folder size={16} /> {folder.parent_id ? '- ' : ''}{folder.name}
              </Button>
            ))}
            {canManage && (
              <Flex gap="2" pt="3">
                <Input value={newFolderName} onChange={(event) => setNewFolderName(event.target.value)} placeholder={t('sopLibrary.newFolderPlaceholder')} />
                <Button className="ops-touch" onClick={createFolder} aria-label={t('sopLibrary.addFolder')}>
                  <Plus size={16} /> {t('sopLibrary.addFolder')}
                </Button>
              </Flex>
            )}
          </VStack>
        </OpsPanel>

        <Box flex="1" w="full">
          <OpsPanel>
            <VStack align="stretch" gap="4">
              <Flex direction={{ base: 'column', md: 'row' }} gap="2">
                <Input value={search} onChange={(event) => setSearch(event.target.value)} placeholder={t('sopLibrary.searchPlaceholder')} />
                <select
                  className="ops-touch"
                  style={{ border: '1px solid #e5e7eb', borderRadius: 8, paddingInline: 12, background: 'white' }}
                  value={category}
                  onChange={(event) => setCategory(event.currentTarget.value)}
                >
                  <option value="">{t('sopLibrary.allCategories')}</option>
                  {categories.map((item) => (
                    <option key={item} value={item}>{item}</option>
                  ))}
                </select>
              </Flex>

              <Flex justify="space-between" align="center" wrap="wrap" gap="2">
                <HStack gap="2" wrap="wrap">
                  <StatusPill tone="blue">{t('sopLibrary.visibleCount').replace('{count}', String(visibleSOPs.length))}</StatusPill>
                  <StatusPill tone="neutral">{showArchived ? t('sopLibrary.archivedView') : t('sopLibrary.activeView')}</StatusPill>
                </HStack>
                <Button className="ops-touch" size="sm" variant={showArchived ? 'subtle' : 'outline'} onClick={() => setShowArchived((value) => !value)}>
                  {showArchived ? t('sopLibrary.showActive') : t('sopLibrary.showArchived')}
                </Button>
              </Flex>
            </VStack>
          </OpsPanel>

          <Box mt="4">
            {isLoading ? (
              <PageLoading label={t('sopLibrary.loadingSops')} />
            ) : visibleSOPs.length === 0 ? (
              <EmptyAction
                title={t('sopLibrary.noSopsFound')}
                description={t('sopLibrary.noSopsBody')}
                actionLabel={canManage ? t('sopLibrary.createSop') : undefined}
                onAction={canManage ? () => router.push('/sops/new') : undefined}
              />
            ) : (
              <Box
                display="grid"
                gridTemplateColumns={{ base: '1fr', md: 'repeat(2, minmax(0, 1fr))', xl: 'repeat(3, minmax(0, 1fr))' }}
                gap="3"
              >
                {visibleSOPs.map((sop) => (
                  <SOPRow
                    key={sop.id}
                    sop={sop}
                    foldersById={foldersById}
                    canManage={canManage}
                    onOpen={() => router.push(`/sops/${encodeURIComponent(sop.id)}`)}
                    onEdit={() => router.push(`/sops/${encodeURIComponent(sop.id)}/edit`)}
                    onArchive={async () => {
                      await api.archiveManagedSOP(sop.id);
                      await load();
                    }}
                    onUnarchive={async () => {
                      await api.updateManagedSOP(sop.id, { archived: false });
                      await load();
                    }}
                  />
                ))}
              </Box>
            )}
          </Box>
        </Box>
      </Flex>
    </PageShell>
  );
}
