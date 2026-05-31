'use client';

import { useEffect, useRef, useState } from 'react';
import {
  Box,
  Card,
  Flex,
  Heading,
  Text,
  VStack,
  HStack,
  Badge,
  Input,
  Textarea,
  IconButton,
  Button,
  Separator,
  Grid,
} from '@chakra-ui/react';
import {
  AlertTriangle,
  CheckCircle2,
  ClipboardCheck,
  Download,
  FileText,
  Languages,
  Layers,
  ListChecks,
  Printer,
  QrCode,
  Save,
  Send,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Wrench,
} from 'lucide-react';
import { QRCodeSVG } from 'qrcode.react';
import { api, type SOP, type SOPStep } from '@/lib/api';
import { LANGUAGE_REGISTRY, useI18n } from '@/contexts/I18nContext';
import { StepImagePicker } from '@/components/SOPEditor';

interface SOPViewerProps {
  sop: SOP;
  videoId?: string;
  editable?: boolean;
  canViewInternal?: boolean;
  isFinalized?: boolean;
  onEdit?: (sop: SOP) => void;
  onSave?: (sop: SOP) => Promise<void>;
  onFinalize?: (sop: SOP) => Promise<void>;
}

// Icons
function EditIcon() {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z" />
      <path d="m15 5 4 4" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}

function CloseIcon() {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M18 6 6 18" />
      <path d="m6 6 12 12" />
    </svg>
  );
}

function ToolIcon() {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
    </svg>
  );
}

function ChecklistIcon() {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M9 11l3 3L22 4" />
      <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
    </svg>
  );
}

function WarningIcon() {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/>
      <path d="M12 9v4"/>
      <path d="M12 17h.01"/>
    </svg>
  );
}

function QRIcon() {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect width="5" height="5" x="3" y="3" rx="1"/><rect width="5" height="5" x="16" y="3" rx="1"/>
      <rect width="5" height="5" x="3" y="16" rx="1"/><path d="M21 16h-3a2 2 0 0 0-2 2v3"/>
      <path d="M21 21v.01"/><path d="M12 7v3a2 2 0 0 1-2 2H7"/><path d="M3 12h.01"/><path d="M12 3h.01"/>
      <path d="M12 16v.01"/><path d="M16 12h1"/><path d="M21 12v.01"/><path d="M12 21v-1"/>
    </svg>
  );
}

function ArrowUpIcon() {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="m18 15-6-6-6 6"/>
    </svg>
  );
}

function ArrowDownIcon() {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="m6 9 6 6 6-6"/>
    </svg>
  );
}

function PlusIcon() {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 5v14M5 12h14"/>
    </svg>
  );
}

function TrashIcon() {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>
      <path d="M10 11v6M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>
    </svg>
  );
}

function ShieldCheckIcon() {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><polyline points="9 12 11 14 15 10"/>
    </svg>
  );
}

function ScissorsIcon() {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="6" cy="6" r="3"/><circle cx="6" cy="18" r="3"/>
      <line x1="20" y1="4" x2="8.12" y2="15.88"/><line x1="14.47" y1="14.48" x2="20" y2="20"/>
      <line x1="8.12" y1="8.12" x2="12" y2="12"/>
    </svg>
  );
}

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

const CORRECTION_CATEGORIES = [
  'wrong_action',
  'wrong_tool',
  'sequence_error',
  'missing_detail',
  'unclear',
  'hallucination',
  'other',
] as const;
type CorrectionCategory = (typeof CORRECTION_CATEGORIES)[number];

function categoryLabelKey(category: CorrectionCategory): string {
  switch (category) {
    case 'wrong_action': return 'sop.categoryWrongAction';
    case 'wrong_tool': return 'sop.categoryWrongTool';
    case 'sequence_error': return 'sop.categorySequenceError';
    case 'missing_detail': return 'sop.categoryMissingDetail';
    case 'unclear': return 'sop.categoryUnclear';
    case 'hallucination': return 'sop.categoryHallucination';
    case 'other': return 'sop.categoryOther';
  }
}

// Map a per-step issue category to the backend failure_type taxonomy.
// Used when summarizing the SOP-level correction record sent to /failures.
function categoriesToFailureType(
  categories: Iterable<string | null | undefined>,
): 'hallucination' | 'wrong_order' | 'missing_step' | 'bad_formatting' | 'wrong_answer' {
  const set = new Set<string>();
  for (const c of categories) if (c) set.add(c);
  if (set.has('hallucination')) return 'hallucination';
  if (set.has('sequence_error')) return 'wrong_order';
  if (set.has('missing_detail')) return 'missing_step';
  if (set.has('unclear')) return 'bad_formatting';
  return 'wrong_answer';
}

interface StepCardProps {
  step: SOPStep;
  originalStep?: SOPStep;
  totalSteps: number;
  isFirst: boolean;
  isLast: boolean;
  autoOpenEdit?: boolean;
  editable: boolean;
  excludedTools: Set<string>;
  onStepChange: (step: SOPStep) => void;
  onImageReplaced: (imageUrl: string | undefined) => void;
  onUploadingChange: (uploading: boolean) => void;
  onToggleTool: (tool: string) => void;
  onToggleWrong: () => void;
  onCorrectionNoteChange: (note: string) => void;
  onCorrectionCategoryChange: (category: string) => void;
  onMoveUp: () => void;
  onMoveDown: () => void;
  onDelete: () => void;
  onAddAfter: () => void;
  onVerify: () => void;
  onSplitStep: (first: Partial<SOPStep>, second: Partial<SOPStep>) => void;
  showInternal: boolean;
}

function StepCard({
  step,
  originalStep,
  totalSteps,
  isFirst,
  isLast,
  autoOpenEdit = false,
  editable,
  excludedTools,
  onStepChange,
  onImageReplaced,
  onUploadingChange,
  onToggleTool,
  onToggleWrong,
  onCorrectionNoteChange,
  onCorrectionCategoryChange,
  onMoveUp,
  onMoveDown,
  onDelete,
  onAddAfter,
  onVerify,
  onSplitStep,
  showInternal,
}: StepCardProps) {
  const { t } = useI18n();
  const [isEditing, setIsEditing] = useState(false);
  const [editedStep, setEditedStep] = useState<SOPStep>(step);
  // Track image-load failure per render so we can hide the broken-image
  // box on legacy SOPs whose frames were cleaned from R2 before the
  // ef8569d fix. New SOPs work normally.
  // Resets when image_url changes so a previous broken state doesn't
  // suppress a freshly-saved image.
  const [imageBroken, setImageBroken] = useState(false);
  useEffect(() => {
    setImageBroken(false);
  }, [step.image_url]);
  // Two-click destructive confirm — first click arms the button, second
  // click commits. Resets after 4s if not confirmed.
  const [pendingDelete, setPendingDelete] = useState(false);
  // Split mode replaces the single-step editor with two stacked editors
  // (one per resulting step). Confirming creates the new pair.
  const [isSplitting, setIsSplitting] = useState(false);
  const [splitSecond, setSplitSecond] = useState<{ title: string; description: string; tools: string; checks: string }>({
    title: '',
    description: '',
    tools: '',
    checks: '',
  });

  // Auto-open edit mode for steps inserted via Add step.
  // The parent toggles autoOpenEdit on the new step's first render so the
  // reviewer can immediately fill in the blank step without an extra click.
  useEffect(() => {
    if (autoOpenEdit) setIsEditing(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Sync local editor state with the step prop when the prop changes due
  // to a structural op (reorder/delete). Without this, a StepCard at a
  // given list index can keep showing stale local state after siblings
  // are reordered. We do NOT overwrite an in-progress edit.
  useEffect(() => {
    if (!isEditing) setEditedStep(step);
  }, [step, isEditing]);

  // Auto-clear the pending-delete confirmation if the user hesitates.
  useEffect(() => {
    if (!pendingDelete) return;
    const handle = window.setTimeout(() => setPendingDelete(false), 4000);
    return () => window.clearTimeout(handle);
  }, [pendingDelete]);

  const handleSave = () => {
    onStepChange(editedStep);
    setIsEditing(false);
  };

  const handleCancel = () => {
    setEditedStep(step);
    setIsEditing(false);
    setIsSplitting(false);
  };

  const handleDeleteClick = () => {
    if (pendingDelete) {
      onDelete();
      setPendingDelete(false);
    } else {
      setPendingDelete(true);
    }
  };

  const handleStartSplit = () => {
    setSplitSecond({ title: '', description: '', tools: '', checks: '' });
    setIsSplitting(true);
  };

  const handleConfirmSplit = () => {
    onSplitStep(
      {
        title: editedStep.title,
        description: editedStep.description,
        tools: editedStep.tools,
        checks: editedStep.checks,
      },
      {
        title: splitSecond.title.trim(),
        description: splitSecond.description.trim(),
        tools: splitSecond.tools.split(',').map((s) => s.trim()).filter(Boolean),
        checks: splitSecond.checks.split(',').map((s) => s.trim()).filter(Boolean),
      },
    );
    setIsSplitting(false);
    setIsEditing(false);
  };

  // Cache-buster + crossOrigin drop:
  //   - Bust the browser cache when the URL itself changes by appending
  //     the URL as the <img> React key. This forces a fresh <img> node
  //     and a fresh fetch — a negative-cached 404 from before the
  //     auth-drop deploy can't poison a freshly-saved image.
  //   - Skip crossOrigin="anonymous". It put step images on a separate
  //     CORS cache key, and a single misbehaving response (or an
  //     intermediate that strips Access-Control-Allow-Origin) silently
  //     broke every subsequent edit until the user nuked the cache.
  //     html2canvas's useCORS:true path handles the PDF export case
  //     by re-fetching via its own image loader.
  const imageUrl = step.image_url ? `${API_BASE_URL}${step.image_url}` : null;
  const showImage = !!imageUrl;

  if (isEditing) {
    // Show the AI-generated original beneath each field when it differs
    // from the reviewer's current edit — gives them a passive reference
    // without forcing a side-by-side diff.
    const renderOriginal = (originalValue: string | undefined, editedValue: string) => {
      if (!originalStep) return null;
      if (!originalValue) return null;
      if (originalValue === editedValue) return null;
      return (
        <Text fontSize="xs" color="gray.500" fontStyle="italic" mt="1" className="sop-text-wrap">
          AI: {originalValue}
        </Text>
      );
    };
    const originalToolsJoined = originalStep ? originalStep.tools.join(', ') : '';
    const originalChecksJoined = originalStep ? originalStep.checks.join(', ') : '';
    return (
      <Card.Root data-testid={`sop-step-${step.step_number}`} className="sop-step ops-sop-step sop-step-edit-card">
        <Card.Body>
          <VStack align="stretch" gap="3">
            <Flex justify="space-between" align="center" wrap="wrap" gap="2">
              <Flex align="center" gap="2">
                <Box className="sop-step-edit-num">{step.step_number}</Box>
                <Badge colorPalette="blue" variant="subtle" px="2" py="1">
                  {t('sop.stepOfTotal').replace('{n}', String(step.step_number)).replace('{total}', String(totalSteps))}
                </Badge>
              </Flex>
              <Flex gap="1" align="center">
                {!isSplitting && (
                  <Button size="xs" variant="outline" colorPalette="purple" onClick={handleStartSplit}>
                    <ScissorsIcon /> {t('sop.splitStep')}
                  </Button>
                )}
                <IconButton aria-label={t('sop.ariaSave')} size="sm" variant="solid" colorPalette="green" onClick={isSplitting ? handleConfirmSplit : handleSave}>
                  <CheckIcon />
                </IconButton>
                <IconButton aria-label={t('sop.ariaCancel')} size="sm" variant="outline" colorPalette="red" onClick={handleCancel}>
                  <CloseIcon />
                </IconButton>
              </Flex>
            </Flex>
            {isSplitting && (
              <Text fontSize="xs" fontWeight="600" color="purple.700">{t('sop.splitFirstPart')}</Text>
            )}
            <Box>
              <Text className="sop-form-label" mb="1">{t('sop.stepTitlePlaceholder')}</Text>
              <Input className="ops-input" value={editedStep.title} onChange={(e) => setEditedStep({ ...editedStep, title: e.target.value })} placeholder={t('sop.stepTitlePlaceholder')} fontWeight="semibold" />
              {renderOriginal(originalStep?.title, editedStep.title)}
            </Box>
            <Box>
              <Text className="sop-form-label" mb="1">{t('sop.stepDescPlaceholder')}</Text>
              <Textarea className="ops-input" value={editedStep.description} onChange={(e) => setEditedStep({ ...editedStep, description: e.target.value })} placeholder={t('sop.stepDescPlaceholder')} rows={3} />
              {renderOriginal(originalStep?.description, editedStep.description)}
            </Box>
            <StepImagePicker
              imageUrl={editedStep.image_url}
              onChange={(next) => {
                const nextUrl = next ?? undefined;
                // Reflect in the in-progress edit so the editor preview updates...
                setEditedStep((prev) => ({ ...prev, image_url: nextUrl }));
                // ...and persist to the parent localSop immediately. Without
                // this, replacing an image and clicking Finalize (without
                // first hitting the green save check) would commit the OLD
                // image_url — usually a deleted AI frame on draft SOPs.
                onImageReplaced(nextUrl);
              }}
              onUploadingChange={onUploadingChange}
              label={t('sopEditor.stepPictureLabel')}
              hint={t('sopEditor.stepPictureHint')}
              captureLabel={t('sopEditor.stepPictureCapture')}
              uploadLabel={t('sopEditor.stepPictureUpload')}
              removeLabel={t('sopEditor.stepPictureRemove')}
            />
            <Box>
              <Text className="sop-form-label" mb="1">{t('sop.toolsLabel')}</Text>
              <Input className="ops-input" value={editedStep.tools.join(', ')} onChange={(e) => setEditedStep({ ...editedStep, tools: e.target.value.split(',').map((t) => t.trim()).filter(Boolean) })} placeholder={t('sop.toolsPlaceholder')} />
              {renderOriginal(originalToolsJoined, editedStep.tools.join(', '))}
            </Box>
            <Box>
              <Text className="sop-form-label" mb="1">{t('sop.checksLabel')}</Text>
              <Input className="ops-input" value={editedStep.checks.join(', ')} onChange={(e) => setEditedStep({ ...editedStep, checks: e.target.value.split(',').map((c) => c.trim()).filter(Boolean) })} placeholder={t('sop.checksPlaceholder')} />
              {renderOriginal(originalChecksJoined, editedStep.checks.join(', '))}
            </Box>
            {isSplitting && (
              <Box bg="purple.50" border="1px solid" borderColor="purple.200" borderRadius="md" p="3">
                <Text fontSize="xs" fontWeight="700" color="purple.700" mb="2">{t('sop.splitSecondPart')}</Text>
                <VStack align="stretch" gap="2">
                  <Input
                    value={splitSecond.title}
                    onChange={(e) => setSplitSecond({ ...splitSecond, title: e.target.value })}
                    placeholder={t('sop.newStepPlaceholder')}
                    fontWeight="semibold"
                    bg="white"
                  />
                  <Textarea
                    value={splitSecond.description}
                    onChange={(e) => setSplitSecond({ ...splitSecond, description: e.target.value })}
                    placeholder={t('sop.newStepDescPlaceholder')}
                    rows={3}
                    bg="white"
                  />
                  <Input
                    value={splitSecond.tools}
                    onChange={(e) => setSplitSecond({ ...splitSecond, tools: e.target.value })}
                    placeholder={t('sop.toolsLabel')}
                    bg="white"
                  />
                  <Input
                    value={splitSecond.checks}
                    onChange={(e) => setSplitSecond({ ...splitSecond, checks: e.target.value })}
                    placeholder={t('sop.checksLabel')}
                    bg="white"
                  />
                </VStack>
                <HStack mt="3" gap="2" justify="flex-end">
                  <Button size="xs" variant="ghost" onClick={() => setIsSplitting(false)}>
                    {t('sop.splitCancel')}
                  </Button>
                  <Button size="xs" colorPalette="purple" onClick={handleConfirmSplit} disabled={!splitSecond.title.trim()}>
                    {t('sop.splitConfirm')}
                  </Button>
                </HStack>
              </Box>
            )}
          </VStack>
        </Card.Body>
      </Card.Root>
    );
  }

  const confidence = step.confidence ?? 1.0;
  const confidenceColor = confidence >= 0.8 ? 'green' : confidence >= 0.5 ? 'yellow' : 'red';
  const confidencePct = Math.round(confidence * 100);

  return (
    <article
      data-testid={`sop-step-${step.step_number}`}
      className={`sop-step sop-step-v2 ${step.user_marked_wrong ? 'sop-step-v2-wrong' : ''} ${isLast ? 'sop-step-v2-last' : ''}`}
    >
      <div className="sop-step-v2-rail">
        <div className={`sop-step-v2-num ${step.user_marked_wrong ? 'sop-step-v2-num-wrong' : ''}`}>
          {step.step_number}
        </div>
        {!isLast && <div className="sop-step-v2-line" />}
      </div>
      <div className="sop-step-v2-card">
        <div className="sop-step-v2-head">
          <div className="sop-step-v2-titles">
            <div className="sop-step-v2-eyebrow">
              <span className="sop-step-v2-step-of">
                {t('sop.stepBadge').replace('{n}', String(step.step_number)).replace('{total}', String(totalSteps))}
              </span>
              {showInternal && (
                <span className={`sop-step-v2-confidence sop-step-v2-confidence-${confidenceColor}`}>
                  {confidencePct}{t('sop.confidenceSuffix')}
                </span>
              )}
              {step.user_marked_wrong && (
                <span className="sop-step-v2-flag sop-step-v2-flag-red">{t('sop.markedWrong')}</span>
              )}
              {showInternal && step.verified === true && (
                <span className="sop-step-v2-flag sop-step-v2-flag-green">{t('sop.verified')}</span>
              )}
              {showInternal && step.verified === false && (
                <span className="sop-step-v2-flag sop-step-v2-flag-red">{t('sop.unverified')}</span>
              )}
            </div>
            <h4 className="sop-step-v2-title sop-text-wrap" data-testid={`step-${step.step_number}-title`}>
              {step.title}
            </h4>
          </div>
          {editable && (
            <div className="sop-step-v2-actions">
              <IconButton aria-label={t('sop.moveUp')} title={t('sop.moveUp')} size="sm" variant="ghost" onClick={onMoveUp} disabled={isFirst}>
                <ArrowUpIcon />
              </IconButton>
              <IconButton aria-label={t('sop.moveDown')} title={t('sop.moveDown')} size="sm" variant="ghost" onClick={onMoveDown} disabled={isLast}>
                <ArrowDownIcon />
              </IconButton>
              <IconButton aria-label={t('sop.addStepAfter')} title={t('sop.addStepAfter')} size="sm" variant="ghost" colorPalette="blue" onClick={onAddAfter}>
                <PlusIcon />
              </IconButton>
              {showInternal && (
                <IconButton
                  aria-label={step.verified ? t('sop.unverifyStep') : t('sop.verifyStep')}
                  title={step.verified ? t('sop.unverifyStep') : t('sop.verifyStep')}
                  size="sm"
                  variant={step.verified ? 'solid' : 'ghost'}
                  colorPalette="green"
                  onClick={onVerify}
                >
                  <ShieldCheckIcon />
                </IconButton>
              )}
              <Button size="xs" variant={step.user_marked_wrong ? 'solid' : 'outline'} colorPalette="red" onClick={onToggleWrong}>
                {step.user_marked_wrong ? t('sop.wrong') : t('sop.markWrong')}
              </Button>
              <IconButton aria-label={t('sop.ariaEditStep')} size="sm" variant="ghost" onClick={() => setIsEditing(true)}>
                <EditIcon />
              </IconButton>
              <Button
                size="xs"
                variant={pendingDelete ? 'solid' : 'ghost'}
                colorPalette="red"
                onClick={handleDeleteClick}
                title={pendingDelete ? t('sop.confirmDeleteStep') : t('sop.deleteStep')}
              >
                <TrashIcon />
                {pendingDelete ? <Box as="span" ml="1">{t('sop.confirmDeleteStep')}</Box> : null}
              </Button>
            </div>
          )}
        </div>

        <p className="sop-step-v2-desc sop-text-wrap" data-testid={`step-${step.step_number}-description`}>
          {step.description}
        </p>

        {showImage && (
          <div className={`sop-step-v2-thumb ${imageBroken ? 'sop-step-v2-thumb-broken' : ''}`}>
            <img
              key={imageUrl}
              src={imageUrl}
              alt={`Step ${step.step_number}: ${step.title}`}
              onError={() => setImageBroken(true)}
              onLoad={() => setImageBroken(false)}
            />
            {imageBroken && (
              <button
                type="button"
                className="sop-step-v2-thumb-retry"
                onClick={() => {
                  setImageBroken(false);
                  // Force the <img> to re-attempt by mutating the cache-bust
                  // suffix. Cheaper than rerendering the whole tree.
                  const el = document.querySelector(`img[alt="Step ${step.step_number}: ${step.title}"]`) as HTMLImageElement | null;
                  if (el) el.src = `${imageUrl}?t=${Date.now()}`;
                }}
              >
                {t('sop.retryImageLoad')}
              </button>
            )}
          </div>
        )}

        {(step.tools.length > 0 || step.checks.length > 0) && (
          <div className="sop-step-v2-grid">
            {step.tools.length > 0 && (
              <div className="sop-step-v2-panel sop-step-v2-panel-tools">
                <div className="sop-step-v2-panel-head">
                  <Wrench size={14} />
                  <span>{t('sop.requiredToolsHeader')}</span>
                  {editable && (
                    <span className="sop-step-v2-panel-hint">{t('sop.tapToToggleTool')}</span>
                  )}
                </div>
                <div className="sop-step-v2-chips" data-testid={`step-${step.step_number}-tools`}>
                  {step.tools.map((tool, index) => {
                    const excluded = excludedTools.has(tool);
                    return (
                      <button
                        key={index}
                        type="button"
                        className={`sop-step-v2-chip ${excluded ? 'sop-step-v2-chip-excluded' : ''} ${editable ? 'sop-step-v2-chip-button' : 'sop-step-v2-chip-static'}`}
                        onClick={editable ? () => onToggleTool(tool) : undefined}
                        aria-pressed={editable ? !excluded : undefined}
                        disabled={!editable}
                      >
                        {tool}
                      </button>
                    );
                  })}
                </div>
              </div>
            )}
            {step.checks.length > 0 && (
              <div className="sop-step-v2-panel sop-step-v2-panel-checks">
                <div className="sop-step-v2-panel-head">
                  <ListChecks size={14} />
                  <span>{t('sop.qualityVerificationHeader')}</span>
                </div>
                <ul className="sop-step-v2-checks" data-testid={`step-${step.step_number}-checks`}>
                  {step.checks.map((check, index) => (
                    <li key={index}>
                      <CheckIcon />
                      <span className="sop-text-wrap">{check}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}

        {showInternal && step.evidence && step.evidence.length > 0 && (
          <div className="sop-step-v2-evidence">
            <span className="sop-step-v2-evidence-label">{t('sop.evidenceLabel')}</span>
            {step.evidence.map((e, i) => (
              <span key={i} className="sop-step-v2-evidence-chip sop-chip sop-chip-long">{e}</span>
            ))}
          </div>
        )}
        {showInternal && step.verification_quote && (
          <div className="sop-step-v2-quote">
            <Text fontSize="xs" color="green.700" fontStyle="italic" className="sop-text-wrap">
              {t('sop.sourcePrefix')} &ldquo;{step.verification_quote}&rdquo;
            </Text>
          </div>
        )}
        {step.notes && step.notes !== 'null' && (
          <Text fontSize="sm" color="orange.700" fontStyle="italic" className="sop-text-wrap" mt="2">
            {t('sop.notePrefix')} {step.notes}
          </Text>
        )}
        {step.user_correction_note && (
          <div className="sop-step-v2-correction-note">
            <Text fontSize="xs" color="red.700" className="sop-text-wrap">
              {t('sop.reviewerNote')} {step.user_correction_note}
            </Text>
          </div>
        )}
        {editable && step.user_marked_wrong && (
          <Box bg="red.50" p="3" borderRadius="md" border="1px solid" borderColor="red.200" mt="3">
            <Text fontSize="sm" fontWeight="700" color="red.800" mb="2">
              {t('sop.correctionWhatWrong')}
            </Text>
            <Text fontSize="xs" fontWeight="600" color="red.700" mb="1">
              {t('sop.correctionCategoryLabel')}
            </Text>
            <select
              value={step.user_correction_category || ''}
              onChange={(event) => onCorrectionCategoryChange(event.target.value)}
              style={{
                width: '100%',
                height: 36,
                border: '1px solid #fecaca',
                borderRadius: 6,
                paddingInline: 10,
                background: 'white',
                marginBottom: 8,
                fontSize: 14,
              }}
            >
              <option value="">{t('sop.correctionCategoryPick')}</option>
              {CORRECTION_CATEGORIES.map((category) => (
                <option key={category} value={category}>{t(categoryLabelKey(category))}</option>
              ))}
            </select>
            <Textarea
              value={step.user_correction_note || ''}
              onChange={(event) => onCorrectionNoteChange(event.target.value)}
              placeholder={t('sop.correctionNotePlaceholder')}
              rows={2}
              bg="white"
            />
          </Box>
        )}
      </div>
    </article>
  );
}


// Finalize confirmation — replaces window.confirm with an inline summary
// of which steps will be logged as training data when the SOP is locked.
function FinalizeConfirmModal({
  isOpen,
  onClose,
  onConfirm,
  editedCount,
  markedWrongCount,
  addedCount,
  deletedCount,
  reorderedCount,
  verifiedCount,
  isBusy,
}: {
  isOpen: boolean;
  onClose: () => void;
  onConfirm: () => void;
  editedCount: number;
  markedWrongCount: number;
  addedCount: number;
  deletedCount: number;
  reorderedCount: number;
  verifiedCount: number;
  isBusy: boolean;
}) {
  const { t } = useI18n();
  if (!isOpen) return null;
  const hasChanges = editedCount > 0 || markedWrongCount > 0 || addedCount > 0 || deletedCount > 0 || reorderedCount > 0 || verifiedCount > 0;
  return (
    <Box position="fixed" inset="0" bg="blackAlpha.600" zIndex="1100" display="flex" alignItems="center" justifyContent="center" onClick={onClose}>
      <Card.Root maxW="440px" mx="4" onClick={(e) => e.stopPropagation()}>
        <Card.Body>
          <VStack align="stretch" gap="4">
            <Heading size="md" color="gray.800">{t('sop.finalizeConfirmTitle')}</Heading>
            <Text fontSize="sm" color="gray.700">{t('sop.finalizeConfirmBody')}</Text>
            {hasChanges ? (
              <Box bg="blue.50" border="1px solid" borderColor="blue.200" borderRadius="md" p="3">
                <VStack align="stretch" gap="1">
                  {editedCount > 0 && (
                    <Text fontSize="sm" color="gray.800">
                      - {t('sop.finalizeSummaryEdited').replace('{count}', String(editedCount))}
                    </Text>
                  )}
                  {markedWrongCount > 0 && (
                    <Text fontSize="sm" color="gray.800">
                      - {t('sop.finalizeSummaryMarkedWrong').replace('{count}', String(markedWrongCount))}
                    </Text>
                  )}
                  {addedCount > 0 && (
                    <Text fontSize="sm" color="gray.800">
                      - {t('sop.addStepAfter')}: {addedCount}
                    </Text>
                  )}
                  {deletedCount > 0 && (
                    <Text fontSize="sm" color="gray.800">
                      - {t('sop.deleteStep')}: {deletedCount}
                    </Text>
                  )}
                  {reorderedCount > 0 && (
                    <Text fontSize="sm" color="gray.800">
                      - {t('sop.moveUp')}/{t('sop.moveDown')}: {reorderedCount}
                    </Text>
                  )}
                  {verifiedCount > 0 && (
                    <Text fontSize="sm" color="gray.800">
                      - {t('sop.verifyStep')}: {verifiedCount}
                    </Text>
                  )}
                </VStack>
                <Text fontSize="xs" color="blue.700" mt="2">
                  {t('sop.finalizeSavedAsTraining')}
                </Text>
              </Box>
            ) : (
              <Text fontSize="xs" color="gray.500" fontStyle="italic">{t('sop.finalizeNoChanges')}</Text>
            )}
            <HStack gap="2" justify="flex-end">
              <Button size="sm" variant="ghost" onClick={onClose} disabled={isBusy}>{t('sop.cancel')}</Button>
              <Button size="sm" colorPalette="green" onClick={onConfirm} loading={isBusy}>
                {t('sop.finalizeConfirm')}
              </Button>
            </HStack>
          </VStack>
        </Card.Body>
      </Card.Root>
    </Box>
  );
}

// Correction-feedback modal — replaces window.prompt with a categorized
// form so reviewers can tag the failure mode and add a free-form note.
function CorrectionReasonModal({
  isOpen,
  onClose,
  onSubmit,
  isBusy,
}: {
  isOpen: boolean;
  onClose: () => void;
  onSubmit: (payload: { category: string; note: string }) => void;
  isBusy: boolean;
}) {
  const { t } = useI18n();
  const [category, setCategory] = useState<string>('');
  const [note, setNote] = useState('');

  if (!isOpen) return null;
  const handleSubmit = () => {
    onSubmit({ category, note });
  };

  return (
    <Box position="fixed" inset="0" bg="blackAlpha.600" zIndex="1100" display="flex" alignItems="center" justifyContent="center" onClick={onClose}>
      <Card.Root maxW="480px" mx="4" onClick={(e) => e.stopPropagation()}>
        <Card.Body>
          <VStack align="stretch" gap="3">
            <Heading size="md" color="gray.800">{t('sop.submitCorrectionTitle')}</Heading>
            <Text fontSize="sm" color="gray.700">{t('sop.submitCorrectionBody')}</Text>
            <Box>
              <Text fontSize="xs" fontWeight="700" color="gray.600" mb="1">
                {t('sop.correctionCategoryLabel')}
              </Text>
              <select
                value={category}
                onChange={(event) => setCategory(event.target.value)}
                style={{
                  width: '100%',
                  height: 40,
                  border: '1px solid #e2e8f0',
                  borderRadius: 8,
                  paddingInline: 12,
                  background: 'white',
                  fontSize: 14,
                }}
              >
                <option value="">{t('sop.correctionCategoryPick')}</option>
                {CORRECTION_CATEGORIES.map((c) => (
                  <option key={c} value={c}>{t(categoryLabelKey(c))}</option>
                ))}
              </select>
            </Box>
            <Box>
              <Text fontSize="xs" fontWeight="700" color="gray.600" mb="1">
                {t('sop.submitCorrectionReasonLabel')}
              </Text>
              <Textarea
                value={note}
                onChange={(event) => setNote(event.target.value)}
                placeholder={t('sop.submitCorrectionReasonPlaceholder')}
                rows={3}
                bg="white"
              />
            </Box>
            <HStack gap="2" justify="flex-end">
              <Button size="sm" variant="ghost" onClick={onClose} disabled={isBusy}>{t('sop.cancel')}</Button>
              <Button size="sm" colorPalette="orange" onClick={handleSubmit} loading={isBusy}>
                {t('sop.submitCorrectionSend')}
              </Button>
            </HStack>
          </VStack>
        </Card.Body>
      </Card.Root>
    </Box>
  );
}

// QR Code Modal Component
function QRCodeModal({ isOpen, onClose, url, title }: { isOpen: boolean; onClose: () => void; url: string; title: string }) {
  const { t } = useI18n();
  if (!isOpen) return null;

  const handleCopyLink = () => {
    navigator.clipboard.writeText(url);
    alert(t('sop.linkCopied'));
  };

  return (
    <Box position="fixed" top="0" left="0" right="0" bottom="0" bg="blackAlpha.600" zIndex="1000" display="flex" alignItems="center" justifyContent="center" onClick={onClose}>
      <Card.Root maxW="400px" mx="4" onClick={(e) => e.stopPropagation()}>
        <Card.Body>
          <VStack gap="4">
            <Heading size="md">{t('sop.shareSop')}</Heading>
            <Text fontSize="sm" color="gray.600" textAlign="center">{title}</Text>
            <Box p="4" bg="white" borderRadius="lg" border="1px solid" borderColor="gray.200">
              <QRCodeSVG value={url} size={200} level="H" includeMargin={true} />
            </Box>
            <Text fontSize="xs" color="gray.500" textAlign="center">{t('sop.scanQrToAccess')}</Text>
            <HStack gap="2" width="100%">
              <Button flex="1" size="sm" variant="outline" onClick={handleCopyLink}>{t('sop.copyLink')}</Button>
              <Button flex="1" size="sm" colorPalette="blue" onClick={onClose}>{t('sop.done')}</Button>
            </HStack>
          </VStack>
        </Card.Body>
      </Card.Root>
    </Box>
  );
}

export function SOPViewer({
  sop,
  videoId,
  editable = false,
  canViewInternal = false,
  isFinalized = false,
  onEdit,
  onSave,
  onFinalize,
}: SOPViewerProps) {
  const { t, locale } = useI18n();
  const [localSop, setLocalSop] = useState<SOP>(sop);
  const [isEditingHeader, setIsEditingHeader] = useState(false);
  const [editedTitle, setEditedTitle] = useState(sop.title);
  const [editedDescription, setEditedDescription] = useState(sop.description);
  const [showQRModal, setShowQRModal] = useState(false);
  // Per-step set of tools the user has marked as "not actually used".
  // Applied (filtered out) at save / finalize time.
  const [excludedToolsByStep, setExcludedToolsByStep] = useState<Record<number, Set<string>>>({});
  const [isDirty, setIsDirty] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [isFinalizing, setIsFinalizing] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [isSubmittingCorrection, setIsSubmittingCorrection] = useState(false);
  const [correctionStatus, setCorrectionStatus] = useState<null | 'sent' | 'error'>(null);
  const [showFinalizeModal, setShowFinalizeModal] = useState(false);
  const [showCorrectionModal, setShowCorrectionModal] = useState(false);
  const [newlyAddedStepNumber, setNewlyAddedStepNumber] = useState<number | null>(null);
  const [isTranslating, setIsTranslating] = useState(false);
  const [translationError, setTranslationError] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<'operator' | 'detailed'>('operator');
  // Live count of in-flight step-image uploads. Save Changes / Finalize
  // are disabled while > 0 so we never commit a localSop snapshot taken
  // before onChange had a chance to write the new image_url back.
  const [uploadingCount, setUploadingCount] = useState(0);
  const printRef = useRef<HTMLDivElement>(null);
  const originalSopRef = useRef<SOP>(sop);

  // Editing is only allowed before finalization.
  const canEdit = editable && !isFinalized;
  const showInternal = canViewInternal && viewMode === 'detailed';

  const currentDate = new Date().toLocaleDateString('en-US', { year: 'numeric', month: 'long', day: 'numeric' });
  const sopUrl = typeof window !== 'undefined' ? window.location.href : '';
  const generationMetadata = localSop.generation_metadata || {};
  const synthesisModel = generationMetadata.synthesis_model;
  const visionModel = generationMetadata.vision_model;
  const verificationModel = generationMetadata.verification_model;
  const synthesisMode = generationMetadata.synthesis_mode;

  // "All Tools" summary respects the per-step exclusions.
  const allTools = [
    ...new Set(
      localSop.steps.flatMap((step) =>
        step.tools.filter((t) => !(excludedToolsByStep[step.step_number]?.has(t)))
      )
    ),
  ];

  const buildPendingSop = (): SOP => ({
    ...localSop,
    steps: localSop.steps.map((step) => ({
      ...step,
      tools: step.tools.filter((t) => !(excludedToolsByStep[step.step_number]?.has(t))),
    })),
  });

  // Reassigns step_number 1..N after any structural change (add/delete/reorder).
  const reorderSteps = (steps: SOPStep[]): SOPStep[] =>
    steps.map((s, i) => ({ ...s, step_number: i + 1 }));

  const handleDeleteStep = (stepNumber: number) => {
    const updatedSteps = reorderSteps(localSop.steps.filter((s) => s.step_number !== stepNumber));
    const updatedSop = { ...localSop, steps: updatedSteps };
    setLocalSop(updatedSop);
    onEdit?.(updatedSop);
    setIsDirty(true);
  };

  const handleMoveStepUp = (stepNumber: number) => {
    const index = localSop.steps.findIndex((s) => s.step_number === stepNumber);
    if (index <= 0) return;
    const steps = [...localSop.steps];
    [steps[index - 1], steps[index]] = [steps[index], steps[index - 1]];
    const updatedSop = { ...localSop, steps: reorderSteps(steps) };
    setLocalSop(updatedSop);
    onEdit?.(updatedSop);
    setIsDirty(true);
  };

  const handleMoveStepDown = (stepNumber: number) => {
    const index = localSop.steps.findIndex((s) => s.step_number === stepNumber);
    if (index >= localSop.steps.length - 1) return;
    const steps = [...localSop.steps];
    [steps[index], steps[index + 1]] = [steps[index + 1], steps[index]];
    const updatedSop = { ...localSop, steps: reorderSteps(steps) };
    setLocalSop(updatedSop);
    onEdit?.(updatedSop);
    setIsDirty(true);
  };

  const handleAddStepAfter = (stepNumber: number) => {
    const index = localSop.steps.findIndex((s) => s.step_number === stepNumber);
    const newStep: SOPStep = { step_number: 0, title: '', description: '', tools: [], checks: [] };
    const steps = [...localSop.steps];
    steps.splice(index + 1, 0, newStep);
    const reordered = reorderSteps(steps);
    const updatedSop = { ...localSop, steps: reordered };
    setLocalSop(updatedSop);
    onEdit?.(updatedSop);
    setIsDirty(true);
    setNewlyAddedStepNumber(reordered[index + 1].step_number);
  };

  const handleVerifyStep = (stepNumber: number) => {
    const updatedSteps = localSop.steps.map((s) =>
      s.step_number === stepNumber ? { ...s, verified: !s.verified } : s,
    );
    const updatedSop = { ...localSop, steps: updatedSteps };
    setLocalSop(updatedSop);
    onEdit?.(updatedSop);
    setIsDirty(true);
  };

  const handleSplitStep = (stepNumber: number, first: Partial<SOPStep>, second: Partial<SOPStep>) => {
    const index = localSop.steps.findIndex((s) => s.step_number === stepNumber);
    const original = localSop.steps[index];
    const firstStep: SOPStep = {
      ...original,
      title: first.title ?? original.title,
      description: first.description ?? original.description,
      tools: first.tools ?? original.tools,
      checks: first.checks ?? [],
    };
    const secondStep: SOPStep = {
      step_number: 0,
      title: second.title ?? '',
      description: second.description ?? '',
      tools: second.tools ?? [],
      checks: second.checks ?? [],
    };
    const steps = [...localSop.steps];
    steps.splice(index, 1, firstStep, secondStep);
    const updatedSop = { ...localSop, steps: reorderSteps(steps) };
    setLocalSop(updatedSop);
    onEdit?.(updatedSop);
    setIsDirty(true);
  };

  const handleToggleTool = (stepNumber: number, tool: string) => {
    setExcludedToolsByStep((prev) => {
      const next = new Set(prev[stepNumber] || []);
      if (next.has(tool)) next.delete(tool);
      else next.add(tool);
      return { ...prev, [stepNumber]: next };
    });
    setIsDirty(true);
  };

  const wrongStepsFrom = (candidate: SOP) =>
    candidate.steps.filter((step) => step.user_marked_wrong);

  // A step counts as "edited" only when its substantive text fields
  // (title/description) differ from the AI-generated original. We skip
  // tool/check arrays here so the chip-toggle "include/exclude tool" UX
  // doesn't silently fire correction submissions every time an operator
  // tunes the tool list. Real corrections to tools/checks should be
  // surfaced via Mark wrong + category.
  const isStepEdited = (step: SOPStep): boolean => {
    const orig = originalSopRef.current.steps.find((s) => s.step_number === step.step_number);
    if (!orig) return true;
    if (step.title !== orig.title) return true;
    if (step.description !== orig.description) return true;
    return false;
  };

  const editedStepsFrom = (candidate: SOP) =>
    candidate.steps.filter((step) => isStepEdited(step) && !step.user_marked_wrong);

  const headerEdited = (candidate: SOP): boolean => {
    const orig = originalSopRef.current;
    return candidate.title !== orig.title || candidate.description !== orig.description;
  };

  // Detect add/delete/reorder/split via title-based matching against the
  // original. Title is the best identity signal we have without stable IDs.
  // Returns three lists used both for hasCorrections gating and for the
  // training-data notes that go to /failures.
  const structuralDiff = (candidate: SOP): {
    deleted: Array<{ step_number: number; title: string }>;
    added: Array<{ step_number: number; title: string }>;
    reorderedFromOrigPosition: number[];
  } => {
    const orig = originalSopRef.current.steps;
    const candTitles = candidate.steps.map((s) => s.title);
    const origTitles = orig.map((s) => s.title);
    const deleted = orig
      .filter((s) => !candTitles.includes(s.title))
      .map((s) => ({ step_number: s.step_number, title: s.title }));
    const added = candidate.steps
      .filter((s) => !origTitles.includes(s.title))
      .map((s) => ({ step_number: s.step_number, title: s.title }));
    const reorderedFromOrigPosition: number[] = [];
    if (deleted.length === 0 && added.length === 0 && candidate.steps.length === orig.length) {
      candidate.steps.forEach((s, idx) => {
        if (orig[idx]?.title !== s.title) reorderedFromOrigPosition.push(s.step_number);
      });
    }
    return { deleted, added, reorderedFromOrigPosition };
  };

  const verifiedStepsFrom = (candidate: SOP) =>
    candidate.steps.filter((step) => step.verified === true).map((s) => s.step_number);

  const correctionNotesFor = (candidate: SOP, extraNote?: string) => {
    const wrongStepNotes = wrongStepsFrom(candidate).map((step) => ({
      step_number: step.step_number,
      title: step.title,
      category: step.user_correction_category || null,
      note: step.user_correction_note || '',
    }));
    const editedStepNotes = editedStepsFrom(candidate).map((step) => ({
      step_number: step.step_number,
      title: step.title,
    }));
    const structural = structuralDiff(candidate);
    return [
      'Step-level correction from SOP editor.',
      `wrong_steps=${JSON.stringify(wrongStepNotes)}`,
      `edited_steps=${JSON.stringify(editedStepNotes)}`,
      structural.deleted.length > 0 ? `deleted_steps=${JSON.stringify(structural.deleted)}` : '',
      structural.added.length > 0 ? `added_steps=${JSON.stringify(structural.added)}` : '',
      structural.reorderedFromOrigPosition.length > 0
        ? `reordered_step_numbers=${JSON.stringify(structural.reorderedFromOrigPosition)}`
        : '',
      verifiedStepsFrom(candidate).length > 0
        ? `verified_steps=${JSON.stringify(verifiedStepsFrom(candidate))}`
        : '',
      headerEdited(candidate) ? 'header_edited=true' : '',
      extraNote ? `reviewer_note=${extraNote}` : '',
    ].filter(Boolean).join('\n');
  };

  const hasStructuralChanges = (candidate: SOP): boolean => {
    const d = structuralDiff(candidate);
    return d.deleted.length > 0 || d.added.length > 0 || d.reorderedFromOrigPosition.length > 0;
  };

  const hasCorrections = (candidate: SOP) =>
    wrongStepsFrom(candidate).length > 0
    || editedStepsFrom(candidate).length > 0
    || headerEdited(candidate)
    || hasStructuralChanges(candidate);

  const submitStepCorrections = async (corrected: SOP, extraNote?: string) => {
    if (!videoId || !hasCorrections(corrected)) return;
    // Structural changes carry their own signal about what went wrong:
    // deleted = AI invented (hallucination), added = AI missed (missing_step),
    // pure reorder = wrong_order. Fall back to per-step categories otherwise.
    const structural = structuralDiff(corrected);
    let failureType: 'hallucination' | 'wrong_order' | 'missing_step' | 'bad_formatting' | 'wrong_answer';
    if (structural.deleted.length > 0) failureType = 'hallucination';
    else if (structural.added.length > 0) failureType = 'missing_step';
    else if (structural.reorderedFromOrigPosition.length > 0) failureType = 'wrong_order';
    else failureType = categoriesToFailureType(
      wrongStepsFrom(corrected).map((s) => s.user_correction_category),
    );
    await api.markSOPAsCorrection(
      videoId,
      corrected,
      failureType,
      'medium',
      correctionNotesFor(corrected, extraNote),
      originalSopRef.current,
    );
    originalSopRef.current = corrected;
  };

  const handleToggleStepWrong = (stepNumber: number) => {
    const current = localSop.steps.find((step) => step.step_number === stepNumber);
    if (!current) return;
    const nextMarked = !current.user_marked_wrong;
    const updatedSteps = localSop.steps.map((step) =>
      step.step_number === stepNumber
        ? {
            ...step,
            user_marked_wrong: nextMarked,
            user_correction_note: nextMarked ? step.user_correction_note || '' : null,
          }
        : step,
    );
    const updatedSop = { ...localSop, steps: updatedSteps };
    setLocalSop(updatedSop);
    onEdit?.(updatedSop);
    setIsDirty(true);
  };

  const handleCorrectionNoteChange = (stepNumber: number, note: string) => {
    const updatedSteps = localSop.steps.map((step) =>
      step.step_number === stepNumber
        ? { ...step, user_marked_wrong: true, user_correction_note: note }
        : step,
    );
    const updatedSop = { ...localSop, steps: updatedSteps };
    setLocalSop(updatedSop);
    onEdit?.(updatedSop);
    setIsDirty(true);
  };

  const handleCorrectionCategoryChange = (stepNumber: number, category: string) => {
    const updatedSteps = localSop.steps.map((step) =>
      step.step_number === stepNumber
        ? { ...step, user_marked_wrong: true, user_correction_category: category || null }
        : step,
    );
    const updatedSop = { ...localSop, steps: updatedSteps };
    setLocalSop(updatedSop);
    onEdit?.(updatedSop);
    setIsDirty(true);
  };

  const handleStepChange = (updatedStep: SOPStep) => {
    const updatedSteps = localSop.steps.map((s) => s.step_number === updatedStep.step_number ? updatedStep : s);
    const updatedSop = { ...localSop, steps: updatedSteps };
    setLocalSop(updatedSop);
    onEdit?.(updatedSop);
    setIsDirty(true);
  };

  // Propagate just the image_url to localSop as soon as the picker finishes
  // uploading — separate from handleStepChange so an in-progress title/desc
  // edit doesn't overwrite localSop until the user explicitly clicks Save.
  // The image change *is* committed eagerly because the use case (take/
  // upload photo → finalize) doesn't reliably involve clicking the green
  // step-save check first.
  const handleImageReplaced = (stepNumber: number, imageUrl: string | undefined) => {
    const updatedSteps = localSop.steps.map((s) =>
      s.step_number === stepNumber ? { ...s, image_url: imageUrl } : s,
    );
    const updatedSop = { ...localSop, steps: updatedSteps };
    setLocalSop(updatedSop);
    onEdit?.(updatedSop);
    setIsDirty(true);
  };

  const handleHeaderSave = () => {
    const updatedSop = { ...localSop, title: editedTitle, description: editedDescription };
    setLocalSop(updatedSop);
    onEdit?.(updatedSop);
    setIsEditingHeader(false);
    setIsDirty(true);
  };

  const handleSaveChanges = async () => {
    if (!onSave) return;
    setIsSaving(true);
    setActionError(null);
    try {
      const pending = buildPendingSop();
      await submitStepCorrections(pending);
      await onSave(pending);
      setLocalSop(pending);
      setExcludedToolsByStep({});
      setIsDirty(false);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : t('sop.saveFailed'));
    } finally {
      setIsSaving(false);
    }
  };

  const handleFinalizeClick = () => {
    if (!onFinalize) return;
    if (wrongStepsFrom(localSop).length > 0) return;
    setShowFinalizeModal(true);
  };

  const confirmFinalize = async () => {
    if (!onFinalize) return;
    // Belt-and-suspenders: don't allow finalize to commit if a step got
    // marked wrong between opening the modal and confirming.
    if (wrongStepsFrom(localSop).length > 0) {
      setShowFinalizeModal(false);
      return;
    }
    setIsFinalizing(true);
    setActionError(null);
    try {
      const pending = buildPendingSop();
      await submitStepCorrections(pending);
      await onFinalize(pending);
      setLocalSop(pending);
      setExcludedToolsByStep({});
      setIsDirty(false);
      setShowFinalizeModal(false);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : t('sop.finalizeFailed'));
    } finally {
      setIsFinalizing(false);
    }
  };

  const handleHeaderCancel = () => {
    setEditedTitle(localSop.title);
    setEditedDescription(localSop.description);
    setIsEditingHeader(false);
  };

  const handleSubmitAsCorrection = () => {
    if (!videoId) return;
    setShowCorrectionModal(true);
  };

  // Posts the CURRENT (edited) SOP to /failures with the video_id; the
  // backend loads the original generated SOP as actual_output. The user's
  // edits become the "expected" version that future generations can learn
  // from via few-shot RAG.
  const submitCorrectionFromModal = async (payload: { category: string; note: string }) => {
    if (!videoId) return;
    setIsSubmittingCorrection(true);
    setCorrectionStatus(null);
    try {
      const corrected = buildPendingSop();
      const failureType = payload.category
        ? categoriesToFailureType([payload.category])
        : categoriesToFailureType(wrongStepsFrom(corrected).map((s) => s.user_correction_category));
      const reviewerNote = [
        payload.category ? `top_level_category=${payload.category}` : '',
        payload.note ? payload.note : '',
      ].filter(Boolean).join(' / ');
      await api.markSOPAsCorrection(
        videoId,
        corrected,
        failureType,
        'medium',
        correctionNotesFor(corrected, reviewerNote || undefined),
        originalSopRef.current,
      );
      originalSopRef.current = corrected;
      setCorrectionStatus('sent');
      setShowCorrectionModal(false);
    } catch {
      setCorrectionStatus('error');
    } finally {
      setIsSubmittingCorrection(false);
    }
  };

  /**
   * Toggle the SOP's user-facing text between English and Hindi.
   *
   * Reads the current language from generation_metadata.output_language
   * (default 'en'), asks the backend to translate to the other one, and
   * swaps localSop with the result. The backend persists the translation,
   * so a page refresh returns the new language until the user flips back.
   */
  const handleToggleLanguage = async () => {
    if (!videoId || isTranslating) return;
    const meta = (localSop.generation_metadata || {}) as Record<string, unknown>;
    const current = (meta.output_language as string) || 'en';
    // Target is "translate into the user's UI locale" — except when we're
    // already in that locale, in which case offer the English fallback.
    // Generalises the old en↔hi toggle to any number of languages.
    const target: string = current === locale ? 'en' : locale;
    setIsTranslating(true);
    setTranslationError(null);
    try {
      const resp = await api.translateSOP(videoId, target);
      setLocalSop(resp.sop);
      onEdit?.(resp.sop);
    } catch (err) {
      setTranslationError(
        err instanceof Error ? err.message : t('sop.languageSwitchFailed'),
      );
    } finally {
      setIsTranslating(false);
    }
  };

  const handlePrint = () => {
    window.print();
  };

  const handleExportPDF = async () => {
    const element = printRef.current;
    if (!element) return;

    element.classList.add('exporting-pdf');

    try {
      const [{ default: jsPDF }, { default: html2canvas }] = await Promise.all([
        import('jspdf'),
        import('html2canvas'),
      ]);

      const pdf = new jsPDF({ orientation: 'portrait', unit: 'mm', format: 'a4' });
      const pageWidth = pdf.internal.pageSize.getWidth();
      const pageHeight = pdf.internal.pageSize.getHeight();
      const margin = 10;
      const usableWidth = pageWidth - margin * 2;
      const usableHeight = pageHeight - margin * 2;
      const sectionGap = 3;

      // Build a flat list of "atomic" sections. Each is rendered as its
      // own canvas so page breaks land between sections, never mid-text.
      const sections: HTMLElement[] = [];
      for (const child of Array.from(element.children) as HTMLElement[]) {
        const stepCards = Array.from(child.querySelectorAll('.sop-step')) as HTMLElement[];
        if (stepCards.length > 0) {
          // The procedure-steps wrapper — render each step card individually
          stepCards.forEach((s) => sections.push(s));
        } else {
          sections.push(child);
        }
      }

      const renderSection = async (node: HTMLElement) =>
        html2canvas(node, {
          scale: 2,
          useCORS: true,
          backgroundColor: '#ffffff',
          logging: false,
        });

      let cursorY = margin;

      for (const section of sections) {
        const canvas = await renderSection(section);
        const heightMM = (canvas.height * usableWidth) / canvas.width;

        // Section taller than one page → slice into page-sized chunks
        if (heightMM > usableHeight) {
          let pixelOffset = 0;
          const sliceHeightPx = (usableHeight / heightMM) * canvas.height;
          let firstSlice = true;
          while (pixelOffset < canvas.height) {
            const thisSlicePx = Math.min(sliceHeightPx, canvas.height - pixelOffset);
            if (!firstSlice || cursorY > margin) {
              pdf.addPage();
              cursorY = margin;
            }
            const sliceCanvas = document.createElement('canvas');
            sliceCanvas.width = canvas.width;
            sliceCanvas.height = thisSlicePx;
            sliceCanvas.getContext('2d')?.drawImage(
              canvas,
              0, pixelOffset, canvas.width, thisSlicePx,
              0, 0, canvas.width, thisSlicePx,
            );
            const sliceHeightMM = (thisSlicePx * usableWidth) / canvas.width;
            pdf.addImage(
              sliceCanvas.toDataURL('image/png'),
              'PNG', margin, cursorY, usableWidth, sliceHeightMM,
            );
            cursorY += sliceHeightMM;
            pixelOffset += thisSlicePx;
            firstSlice = false;
          }
          cursorY += sectionGap;
          continue;
        }

        // Section fits — start a new page if needed
        if (cursorY + heightMM > pageHeight - margin && cursorY > margin) {
          pdf.addPage();
          cursorY = margin;
        }

        pdf.addImage(
          canvas.toDataURL('image/png'),
          'PNG', margin, cursorY, usableWidth, heightMM,
        );
        cursorY += heightMM + sectionGap;
      }

      pdf.save(`SOP_${localSop.title.replace(/[^a-z0-9]/gi, '_')}.pdf`);
    } catch (err) {
      console.error('PDF export failed:', err);
      alert('PDF export failed. Try the Print PDF button instead.');
    } finally {
      element.classList.remove('exporting-pdf');
    }
  };

  const currentLanguage = ((localSop.generation_metadata as Record<string, unknown> | undefined)?.output_language as string) || 'en';
  // Multi-language toggle: "View in <native name>" where the native
  // name is the user's current UI locale (unless the SOP is already
  // in that locale, in which case we offer English as the fallback).
  const targetLocale = currentLanguage === locale ? 'en' : locale;
  const targetEntry = LANGUAGE_REGISTRY.find((e) => e.code === targetLocale);
  const targetNativeName = targetEntry ? targetEntry.native : 'English';
  const isTranslated = currentLanguage !== 'en';

  return (
    <>
      <div className="sop-doc" data-testid="sop-viewer" ref={printRef}>
        {/* Banners */}
        {isFinalized && (
          <div className="sop-banner sop-banner-success no-print-hide">
            <CheckCircle2 size={20} />
            <div>
              <strong>{t('sop.finalizedAndShareable')}</strong>
              <p>{t('sop.finalizedLockedBody')}</p>
            </div>
          </div>
        )}

        {canEdit && isDirty && (
          <div className="sop-banner sop-banner-warn no-print-hide">
            <AlertTriangle size={20} />
            <div>
              <p>
                {t('sop.unsavedChangesBody')
                  .replace('{save}', t('sop.saveChanges'))
                  .replace('{finalize}', t('sop.finalize'))}
              </p>
            </div>
          </div>
        )}

        {canEdit && uploadingCount > 0 && (
          <div className="sop-banner sop-banner-warn no-print-hide">
            <AlertTriangle size={20} />
            <div>
              <p>{t('sop.uploadInProgress')}</p>
            </div>
          </div>
        )}

        {canEdit && wrongStepsFrom(localSop).length > 0 && (
          <div className="sop-banner sop-banner-danger no-print-hide">
            <AlertTriangle size={20} />
            <div>
              <p>{t('sop.wrongStepsBlockFinalize').replace('{count}', String(wrongStepsFrom(localSop).length))}</p>
            </div>
          </div>
        )}

        {showInternal && localSop.needs_review && (
          <div className="sop-banner sop-banner-danger">
            <ShieldAlert size={20} />
            <div>
              <strong>{t('sop.needsHumanReview')}</strong>
              <p>{t('sop.needsReviewBody')}</p>
            </div>
          </div>
        )}

        {/* Hero header */}
        <section className={`sop-hero ${isFinalized ? 'sop-hero-finalized' : ''}`}>
          <div className="sop-hero-content">
            <div className="sop-hero-eyebrow">
              <span className="sop-hero-pill"><Sparkles size={12} /> {t('sop.standardOperatingProcedure')}</span>
              <span className="sop-hero-dot">·</span>
              <span>SOP-{videoId?.slice(0, 8) || 'DRAFT'}</span>
              <span className="sop-hero-dot">·</span>
              <span>{currentDate}</span>
              <span className="sop-hero-dot">·</span>
              <span className={`sop-hero-status sop-hero-status-${isFinalized ? 'done' : 'draft'}`}>
                {isFinalized ? t('sop.statusFinalized') : t('sop.statusDraft')}
              </span>
              {editable && !isEditingHeader && (
                <IconButton
                  aria-label={t('sop.ariaEditHeader')}
                  size="2xs"
                  variant="ghost"
                  onClick={() => setIsEditingHeader(true)}
                  ml="2"
                >
                  <EditIcon />
                </IconButton>
              )}
            </div>
            {isEditingHeader ? (
              <VStack align="stretch" gap="2" mt="2">
                <Input value={editedTitle} onChange={(e) => setEditedTitle(e.target.value)} placeholder={t('sop.headerTitlePlaceholder')} fontSize="2xl" fontWeight="bold" bg="white" />
                <Textarea value={editedDescription} onChange={(e) => setEditedDescription(e.target.value)} placeholder={t('sop.headerDescPlaceholder')} rows={3} bg="white" />
                <HStack gap="1" justify="flex-end">
                  <IconButton aria-label={t('sop.ariaSaveHeader')} size="sm" variant="solid" colorPalette="green" onClick={handleHeaderSave}><CheckIcon /></IconButton>
                  <IconButton aria-label={t('sop.ariaCancelHeader')} size="sm" variant="outline" colorPalette="red" onClick={handleHeaderCancel}><CloseIcon /></IconButton>
                </HStack>
              </VStack>
            ) : (
              <>
                <h1 className="sop-hero-title sop-text-wrap" data-testid="sop-title">{localSop.title}</h1>
                <p className="sop-hero-desc sop-text-wrap" data-testid="sop-description">{localSop.description}</p>
              </>
            )}
          </div>

          <div className="sop-hero-actions no-print-hide">
            {canViewInternal && (
              <div className="sop-view-toggle">
                <button
                  className={`sop-view-toggle-btn ${viewMode === 'operator' ? 'sop-view-toggle-active' : ''}`}
                  onClick={() => setViewMode('operator')}
                >
                  {t('sop.operatorView')}
                </button>
                <button
                  className={`sop-view-toggle-btn ${viewMode === 'detailed' ? 'sop-view-toggle-active' : ''}`}
                  onClick={() => setViewMode('detailed')}
                >
                  {t('sop.detailedView')}
                </button>
              </div>
            )}
            <div className="sop-toolbar">
              {canEdit && onSave && (
                <button
                  className="sop-tool-btn"
                  onClick={handleSaveChanges}
                  disabled={!isDirty || isSaving || isFinalizing || uploadingCount > 0}
                  title={uploadingCount > 0 ? t('sop.waitingForUpload') : t('sop.saveChanges')}
                >
                  <Save size={16} />
                  <span>{isSaving ? t('sop.saving') : t('sop.saveChanges')}</span>
                </button>
              )}
              {canEdit && onFinalize && (
                <button
                  className="sop-tool-btn sop-tool-btn-primary"
                  onClick={handleFinalizeClick}
                  disabled={isSaving || isFinalizing || uploadingCount > 0 || wrongStepsFrom(localSop).length > 0}
                  title={uploadingCount > 0 ? t('sop.waitingForUpload') : wrongStepsFrom(localSop).length > 0 ? t('sop.finalizeBlockedTooltip') : t('sop.finalize')}
                >
                  <ShieldCheck size={16} />
                  <span>{isFinalizing ? t('sop.finalizing') : t('sop.finalize')}</span>
                </button>
              )}
              {videoId && editable && (
                <button
                  className="sop-tool-btn"
                  onClick={handleSubmitAsCorrection}
                  disabled={isSubmittingCorrection}
                  title={t('sop.submitCorrectionTooltip')}
                >
                  <Send size={16} />
                  <span>
                    {isSubmittingCorrection
                      ? t('sop.sending')
                      : correctionStatus === 'sent'
                      ? t('sop.learningSaved')
                      : correctionStatus === 'error'
                      ? t('sop.retryCorrection')
                      : t('sop.submitCorrections')}
                  </span>
                </button>
              )}
              {videoId && (
                <button
                  className={`sop-tool-btn ${isTranslated ? 'sop-tool-btn-accent' : ''}`}
                  onClick={handleToggleLanguage}
                  disabled={isTranslating}
                  title={t('sop.langSwitchTooltip')}
                >
                  <Languages size={16} />
                  <span>
                    {isTranslating
                      ? t('sop.translating')
                      : t('sop.viewInLanguage').replace('{lang}', targetNativeName)}
                  </span>
                </button>
              )}
              <button className="sop-tool-btn" onClick={() => setShowQRModal(true)} title={t('sop.share')}>
                <QrCode size={16} /><span>{t('sop.share')}</span>
              </button>
              <button className="sop-tool-btn" onClick={handleExportPDF} title={t('sop.exportPdf')}>
                <Download size={16} /><span>{t('sop.exportPdf')}</span>
              </button>
              <button className="sop-tool-btn" onClick={handlePrint} title={t('sop.printPdf')}>
                <Printer size={16} /><span>{t('sop.printPdf')}</span>
              </button>
            </div>
            {(actionError || translationError) && (
              <div className="sop-hero-errors">
                {actionError && <span className="sop-hero-error">{actionError}</span>}
                {translationError && (
                  <span className="sop-hero-error">
                    <strong>{t('sop.translationFailed')}: </strong>{translationError}
                  </span>
                )}
              </div>
            )}
          </div>
        </section>

        {/* Quick stats strip */}
        <div className="sop-quick-stats">
          <div className="sop-quick-stat">
            <span className="sop-quick-stat-icon sop-quick-stat-icon-blue"><Layers size={16} /></span>
            <div>
              <div className="sop-quick-stat-value">{localSop.steps.length}</div>
              <div className="sop-quick-stat-label">{t('sop.procedureStepsSuffix')}</div>
            </div>
          </div>
          <div className="sop-quick-stat">
            <span className="sop-quick-stat-icon sop-quick-stat-icon-purple"><Wrench size={16} /></span>
            <div>
              <div className="sop-quick-stat-value">{allTools.length}</div>
              <div className="sop-quick-stat-label">{t('sop.requiredToolsAndMaterials')}</div>
            </div>
          </div>
          <div className="sop-quick-stat">
            <span className="sop-quick-stat-icon sop-quick-stat-icon-amber"><ShieldAlert size={16} /></span>
            <div>
              <div className="sop-quick-stat-value">{localSop.notes.length}</div>
              <div className="sop-quick-stat-label">{t('sop.safetyNotesAndWarnings')}</div>
            </div>
          </div>
          {showInternal && localSop.overall_confidence !== undefined && (
            <div className="sop-quick-stat">
              <span className={`sop-quick-stat-icon sop-quick-stat-icon-${localSop.overall_confidence >= 0.8 ? 'green' : localSop.overall_confidence >= 0.5 ? 'amber' : 'red'}`}>
                <Sparkles size={16} />
              </span>
              <div>
                <div className="sop-quick-stat-value">{Math.round(localSop.overall_confidence * 100)}%</div>
                <div className="sop-quick-stat-label">{t('sop.overallConfidence')}</div>
              </div>
            </div>
          )}
        </div>

        {/* Main two-column grid */}
        <div className="sop-grid">
          <main className="sop-main">
            <div className="sop-section-head">
              <FileText size={18} />
              <h2>{t('sop.procedure')}</h2>
              <span className="sop-section-count">{localSop.steps.length} {t('sop.procedureStepsSuffix')}</span>
            </div>
            <div className="sop-timeline" data-testid="sop-steps">
              {localSop.steps.map((step, index) => (
                <StepCard
                  key={`step-${index}-${step.step_number}`}
                  step={step}
                  originalStep={originalSopRef.current.steps.find((s) => s.step_number === step.step_number)}
                  totalSteps={localSop.steps.length}
                  isFirst={index === 0}
                  isLast={index === localSop.steps.length - 1}
                  autoOpenEdit={step.step_number === newlyAddedStepNumber}
                  editable={canEdit}
                  excludedTools={excludedToolsByStep[step.step_number] || new Set()}
                  onStepChange={handleStepChange}
                  onImageReplaced={(imageUrl) => handleImageReplaced(step.step_number, imageUrl)}
                  onUploadingChange={(uploading) => setUploadingCount((n) => Math.max(0, n + (uploading ? 1 : -1)))}
                  onToggleTool={(tool) => handleToggleTool(step.step_number, tool)}
                  onToggleWrong={() => handleToggleStepWrong(step.step_number)}
                  onCorrectionNoteChange={(note) => handleCorrectionNoteChange(step.step_number, note)}
                  onCorrectionCategoryChange={(category) => handleCorrectionCategoryChange(step.step_number, category)}
                  onMoveUp={() => handleMoveStepUp(step.step_number)}
                  onMoveDown={() => handleMoveStepDown(step.step_number)}
                  onDelete={() => handleDeleteStep(step.step_number)}
                  onAddAfter={() => handleAddStepAfter(step.step_number)}
                  onVerify={() => handleVerifyStep(step.step_number)}
                  onSplitStep={(first, second) => handleSplitStep(step.step_number, first, second)}
                  showInternal={showInternal}
                />
              ))}
              {canEdit && localSop.steps.length === 0 && (
                <Box>
                  <Button
                    size="sm"
                    variant="outline"
                    colorPalette="blue"
                    onClick={() => {
                      const newStep: SOPStep = { step_number: 1, title: '', description: '', tools: [], checks: [] };
                      const updatedSop = { ...localSop, steps: [newStep] };
                      setLocalSop(updatedSop);
                      onEdit?.(updatedSop);
                      setIsDirty(true);
                      setNewlyAddedStepNumber(1);
                    }}
                  >
                    <PlusIcon /> {t('sop.addStepAfter')}
                  </Button>
                </Box>
              )}
            </div>

          </main>

          <aside className="sop-rail no-print-hide">
            {allTools.length > 0 && (
              <div className="sop-rail-card">
                <div className="sop-rail-card-head">
                  <Wrench size={16} />
                  <h3>{t('sop.requiredToolsAndMaterials')}</h3>
                </div>
                <div className="sop-rail-chips">
                  {allTools.map((tool, index) => (
                    <span key={index} className="sop-rail-chip">{tool}</span>
                  ))}
                </div>
              </div>
            )}

            {localSop.notes.length > 0 && (
              <div className="sop-rail-card sop-rail-card-safety">
                <div className="sop-rail-card-head">
                  <ShieldAlert size={16} />
                  <h3>{t('sop.safetyNotesAndWarnings')}</h3>
                </div>
                <ul className="sop-rail-list" data-testid="sop-notes">
                  {localSop.notes.map((note, index) => (
                    <li key={index} className="sop-text-wrap">{note}</li>
                  ))}
                </ul>
              </div>
            )}

            {showInternal && localSop.warnings && localSop.warnings.length > 0 && (
              <div className="sop-rail-card sop-rail-card-warnings">
                <div className="sop-rail-card-head">
                  <AlertTriangle size={16} />
                  <h3>{t('sop.qualityWarnings')}</h3>
                </div>
                <ul className="sop-rail-list">
                  {localSop.warnings.map((w, i) => (
                    <li key={i} className="sop-text-wrap">{w}</li>
                  ))}
                </ul>
              </div>
            )}

            {showInternal && (typeof synthesisModel === 'string' || typeof visionModel === 'string' || typeof verificationModel === 'string' || typeof synthesisMode === 'string') && (
              <div className="sop-rail-card sop-rail-card-meta">
                <div className="sop-rail-card-head">
                  <Sparkles size={16} />
                  <h3>{t('sop.versionLabel').replace(':', '')}</h3>
                </div>
                <dl className="sop-rail-meta">
                  {typeof synthesisModel === 'string' && (
                    <><dt>{t('sop.sopModelLabel')}</dt><dd>{synthesisModel}</dd></>
                  )}
                  {typeof visionModel === 'string' && (
                    <><dt>{t('sop.visionModelLabel')}</dt><dd>{visionModel}</dd></>
                  )}
                  {typeof verificationModel === 'string' && (
                    <><dt>{t('sop.checkModelLabel')}</dt><dd>{verificationModel}</dd></>
                  )}
                  {typeof synthesisMode === 'string' && (
                    <><dt>{t('sop.modeLabel')}</dt><dd>{synthesisMode}</dd></>
                  )}
                </dl>
              </div>
            )}
          </aside>
        </div>

        {/* Footer for Print */}
        <Box className="print-only" display="none" textAlign="center" py="4" borderTop="1px solid" borderColor="gray.200">
          <Text fontSize="xs" color="gray.500">
            {t('sop.generatedBy')} | {t('sop.documentIdLabel')} SOP-{videoId?.slice(0, 8) || 'DRAFT'} | {currentDate}
          </Text>
          <Text fontSize="xs" color="gray.400" mt="1">
            {t('sop.scanForDigital')}
          </Text>
        </Box>
      </div>

      {/* QR Code Modal */}
      <QRCodeModal isOpen={showQRModal} onClose={() => setShowQRModal(false)} url={sopUrl} title={localSop.title} />

      <FinalizeConfirmModal
        isOpen={showFinalizeModal}
        onClose={() => (isFinalizing ? null : setShowFinalizeModal(false))}
        onConfirm={confirmFinalize}
        editedCount={editedStepsFrom(localSop).length}
        markedWrongCount={wrongStepsFrom(localSop).length}
        addedCount={structuralDiff(localSop).added.length}
        deletedCount={structuralDiff(localSop).deleted.length}
        reorderedCount={structuralDiff(localSop).reorderedFromOrigPosition.length}
        verifiedCount={verifiedStepsFrom(localSop).length}
        isBusy={isFinalizing}
      />

      <CorrectionReasonModal
        isOpen={showCorrectionModal}
        onClose={() => (isSubmittingCorrection ? null : setShowCorrectionModal(false))}
        onSubmit={submitCorrectionFromModal}
        isBusy={isSubmittingCorrection}
      />
    </>
  );
}

export default SOPViewer;
