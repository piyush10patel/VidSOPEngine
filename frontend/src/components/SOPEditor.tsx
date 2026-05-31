'use client';

import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Box, Button, Flex, HStack, Input, Text, Textarea, VStack } from '@chakra-ui/react';
import {
  ArrowDown,
  ArrowUp,
  Camera,
  ImageOff,
  ImagePlus,
  Loader2,
  Plus,
  Trash2,
} from 'lucide-react';
import {
  api,
  type SOP,
  type SOPFolder,
  type SOPResponse,
  type SOPStep,
} from '@/lib/api';
import { InlineAlert, OpsPanel, SectionHeader, StatusPill } from '@/components/ops/OperationalUI';
import { useI18n } from '@/contexts/I18nContext';

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

type Props = {
  initial?: SOPResponse | null;
  onSaved: (sop: SOPResponse) => void;
  onCancel: () => void;
};

const emptyStep = (step_number: number): SOPStep => ({
  step_number,
  title: '',
  description: '',
  tools: [],
  checks: [],
  evidence: [],
  confidence: 1,
  warning: '',
  estimated_time_minutes: null,
  attachments: [],
});

const emptySop: SOP = {
  title: '',
  description: '',
  steps: [emptyStep(1)],
  notes: [],
  warnings: [],
  needs_review: false,
  overall_confidence: 1,
  generation_metadata: {},
  source_type: 'manual',
  tools_materials: [],
  sections: [],
  attachments: [],
};

function listToText(values?: string[]) {
  return (values || []).join(', ');
}

function textToList(value: string) {
  return value.split(',').map((item) => item.trim()).filter(Boolean);
}

function FieldLabel({ children }: { children: ReactNode }) {
  return <Text className="sop-form-label">{children}</Text>;
}

export function StepImagePicker({
  imageUrl,
  onChange,
  onUploadingChange,
  label,
  hint,
  captureLabel,
  uploadLabel,
  removeLabel,
}: {
  imageUrl?: string;
  onChange: (next: string | null) => void;
  onUploadingChange?: (uploading: boolean) => void;
  label: string;
  hint: string;
  captureLabel: string;
  uploadLabel: string;
  removeLabel: string;
}) {
  const captureRef = useRef<HTMLInputElement | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [broken, setBroken] = useState(false);
  // Local preview shown immediately on file pick so the user sees the
  // photo without waiting for the server roundtrip. Cleared once the
  // upload completes (the imageUrl prop takes over).
  const [localPreview, setLocalPreview] = useState<string | null>(null);

  // Clear stale broken/local-preview state whenever the parent swaps in
  // a new persisted URL (e.g., after save+reload, or when a step's
  // image_url changes during edit).
  useEffect(() => {
    setBroken(false);
    setLocalPreview(null);
  }, [imageUrl]);

  // Revoke object URLs on unmount or replacement to avoid leaks.
  useEffect(() => {
    return () => {
      if (localPreview) URL.revokeObjectURL(localPreview);
    };
  }, [localPreview]);

  // Normalize the input file to a max-1600px JPEG. This:
  //   1. Strips HEIC/HEIF from iPhone captures so the SOP viewer
  //      (Chrome/Firefox) can actually render the image.
  //   2. Caps the upload to a sane size — phone cameras emit 10MB+ files
  //      that would hit the 8MB server cap.
  //   3. Honors EXIF orientation via createImageBitmap so portrait
  //      phone photos don't end up sideways after the canvas pass.
  //
  // Path: try createImageBitmap first (fastest, EXIF-aware, decodes HEIC
  // on Safari). On failure fall back to <img>+objectURL. If that also
  // fails on a HEIC source we surface the iPhone setting tip.
  const normalizeImage = async (file: File): Promise<File> => {
    const isHeic = /\.hei[cf]$/i.test(file.name) || /^image\/hei[cf]$/i.test(file.type);

    const drawAndEncode = async (source: CanvasImageSource, srcW: number, srcH: number): Promise<File> => {
      const MAX_DIM = 1600;
      let width = srcW;
      let height = srcH;
      const longest = Math.max(width, height);
      if (longest > MAX_DIM) {
        const scale = MAX_DIM / longest;
        width = Math.round(width * scale);
        height = Math.round(height * scale);
      }
      const canvas = document.createElement('canvas');
      canvas.width = width;
      canvas.height = height;
      const ctx = canvas.getContext('2d');
      if (!ctx) throw new Error('Canvas not available.');
      ctx.drawImage(source, 0, 0, width, height);
      const blob: Blob | null = await new Promise((res) =>
        canvas.toBlob((b) => res(b), 'image/jpeg', 0.85),
      );
      if (!blob) throw new Error('Could not encode the image.');
      return new File([blob], 'step.jpg', { type: 'image/jpeg' });
    };

    // Fast path: createImageBitmap honors EXIF, decodes HEIC on Safari,
    // and avoids the FileReader/base64 round-trip entirely.
    if (typeof createImageBitmap === 'function') {
      try {
        const bitmap = await createImageBitmap(file, {
          imageOrientation: 'from-image',
        } as ImageBitmapOptions);
        try {
          return await drawAndEncode(bitmap, bitmap.width, bitmap.height);
        } finally {
          bitmap.close();
        }
      } catch {
        // Fall through to <img> path
      }
    }

    // Fallback: object URL + Image. Works wherever createImageBitmap
    // doesn't, including older iOS Safari with certain HEIC variants.
    const objectUrl = URL.createObjectURL(file);
    try {
      const img: HTMLImageElement = await new Promise((resolve, reject) => {
        const el = new Image();
        el.onload = () => resolve(el);
        el.onerror = () => reject(new Error('decode-failed'));
        el.src = objectUrl;
      });
      return await drawAndEncode(img, img.naturalWidth, img.naturalHeight);
    } catch (err) {
      if (err instanceof Error && err.message === 'decode-failed') {
        throw new Error(
          isHeic
            ? 'This browser can\'t open HEIC photos. On iPhone: Settings → Camera → Formats → Most Compatible, then try again.'
            : 'Could not decode this image. Try a different photo.',
        );
      }
      throw err;
    } finally {
      URL.revokeObjectURL(objectUrl);
    }
  };

  const handleFile = async (file: File | null | undefined) => {
    if (!file) return;
    setError(null);
    setBroken(false);
    setUploading(true);
    onUploadingChange?.(true);
    let normalized: File | null = null;
    try {
      normalized = await normalizeImage(file);
      // Optimistic local preview from the *normalized* blob so the user
      // sees the same orientation/quality they'll get back from the
      // server. Revoke the previous one if any.
      if (localPreview) URL.revokeObjectURL(localPreview);
      setLocalPreview(URL.createObjectURL(normalized));

      const { image_url } = await api.uploadSOPStepImage(normalized);
      onChange(image_url);
      // Don't clear localPreview here — the imageUrl-change effect above
      // will do it once the parent re-renders with the new prop.
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Upload failed');
      // Wipe optimistic preview on upload failure so the user doesn't
      // see a "saved" image that actually isn't persisted.
      if (localPreview) URL.revokeObjectURL(localPreview);
      setLocalPreview(null);
    } finally {
      setUploading(false);
      onUploadingChange?.(false);
      if (captureRef.current) captureRef.current.value = '';
      if (fileRef.current) fileRef.current.value = '';
    }
  };

  // Prefer the local preview while uploading / immediately after pick.
  // Once imageUrl prop arrives from the parent, the effect above clears
  // localPreview and we fall back to the server-served src.
  const serverSrc = imageUrl ? `${API_BASE_URL}${imageUrl}` : null;
  const src = localPreview || serverSrc;

  return (
    <Box className="sop-form-picture">
      <Flex justify="space-between" align="center" gap="2" wrap="wrap" mb="2">
        <Box>
          <Text className="sop-form-label" mb="0">{label}</Text>
          <Text fontSize="xs" color="gray.500">{hint}</Text>
        </Box>
        <HStack gap="2" flexShrink="0">
          <input
            ref={captureRef}
            type="file"
            accept="image/*"
            capture="environment"
            style={{ display: 'none' }}
            onChange={(e) => handleFile(e.target.files?.[0])}
          />
          <input
            ref={fileRef}
            type="file"
            accept="image/*"
            style={{ display: 'none' }}
            onChange={(e) => handleFile(e.target.files?.[0])}
          />
          <Button
            size="sm"
            variant="outline"
            disabled={uploading}
            onClick={() => captureRef.current?.click()}
            className="ops-touch"
          >
            {uploading ? <Loader2 size={14} className="sop-form-spin" /> : <Camera size={14} />}
            <span>{captureLabel}</span>
          </Button>
          <Button
            size="sm"
            variant="outline"
            disabled={uploading}
            onClick={() => fileRef.current?.click()}
            className="ops-touch"
          >
            <ImagePlus size={14} />
            <span>{uploadLabel}</span>
          </Button>
          {imageUrl && (
            <Button
              size="sm"
              variant="ghost"
              colorPalette="red"
              disabled={uploading}
              onClick={() => { onChange(null); setBroken(false); }}
              className="ops-touch"
            >
              <Trash2 size={14} />
              <span>{removeLabel}</span>
            </Button>
          )}
        </HStack>
      </Flex>
      {error && (
        <Text fontSize="xs" color="red.600" mb="2">{error}</Text>
      )}
      {src && !broken && (
        <Box className="sop-form-picture-preview">
          <img src={src} alt="Step illustration" onError={() => setBroken(true)} />
        </Box>
      )}
      {src && broken && (
        <Box className="sop-form-picture-broken">
          <ImageOff size={18} />
          <Text fontSize="xs">Preview unavailable</Text>
        </Box>
      )}
    </Box>
  );
}

export function SOPEditor({ initial, onSaved, onCancel }: Props) {
  const { t, locale } = useI18n();
  const [sop, setSop] = useState<SOP>(initial?.sop || emptySop);
  const [folderId, setFolderId] = useState(initial?.folder_id || '');
  const [category, setCategory] = useState(initial?.category || 'Uncategorized');
  const [tags, setTags] = useState(listToText(initial?.tags));
  const [visibility, setVisibility] = useState(initial?.visibility_scope || 'private');
  const [status, setStatus] = useState(initial?.status || 'draft');
  const [estimatedDuration, setEstimatedDuration] = useState(String(initial?.estimated_duration_minutes || ''));
  const [folders, setFolders] = useState<SOPFolder[]>([]);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [uploadingSteps, setUploadingSteps] = useState<Set<number>>(new Set());
  const anyUploading = uploadingSteps.size > 0;

  useEffect(() => {
    api.listSOPFolders().then((res) => setFolders(res.folders)).catch(() => undefined);
  }, []);

  const updateStep = (idx: number, patch: Partial<SOPStep>) => {
    setSop((prev) => ({
      ...prev,
      steps: prev.steps.map((step, i) => (i === idx ? { ...step, ...patch } : step)),
    }));
  };

  const addStep = () => {
    setSop((prev) => ({ ...prev, steps: [...prev.steps, emptyStep(prev.steps.length + 1)] }));
  };

  const removeStep = (idx: number) => {
    setSop((prev) => {
      const steps = prev.steps.filter((_, i) => i !== idx);
      return {
        ...prev,
        steps: (steps.length ? steps : [emptyStep(1)]).map((step, i) => ({ ...step, step_number: i + 1 })),
      };
    });
  };

  const moveStep = (idx: number, direction: -1 | 1) => {
    setSop((prev) => {
      const nextIndex = idx + direction;
      if (nextIndex < 0 || nextIndex >= prev.steps.length) return prev;
      const steps = [...prev.steps];
      [steps[idx], steps[nextIndex]] = [steps[nextIndex], steps[idx]];
      return { ...prev, steps: steps.map((step, i) => ({ ...step, step_number: i + 1 })) };
    });
  };

  const save = async (nextStatus = status) => {
    if (!sop.title.trim()) {
      setError(t('sop.titleRequired'));
      return;
    }
    const nextSourceType = initial
      ? (initial.source_type === 'manual' ? 'manual' : 'hybrid')
      : 'manual';
    const cleaned: SOP = {
      ...sop,
      title: sop.title.trim(),
      description: sop.description.trim(),
      steps: sop.steps
        .filter((step) => step.title.trim() || step.description.trim())
        .map((step, index) => ({
          ...step,
          step_number: index + 1,
          title: step.title.trim() || `Step ${index + 1}`,
          description: step.description.trim(),
          tools: step.tools || [],
          checks: step.checks || [],
          evidence: step.evidence || [],
          confidence: step.confidence ?? 1,
        })),
      notes: sop.notes || [],
      warnings: sop.warnings || [],
      generation_metadata: {
        ...(sop.generation_metadata || {}),
        // Preserve an existing output_language if the SOP already has one
        // (e.g., AI-generated SOPs carry "hi" / "en" from the pipeline,
        // translated SOPs carry the target language). When absent — i.e.,
        // freshly-created manual SOPs — fall back to the user's UI locale
        // so derived training/workflow/checklist generation runs in the
        // language the operator is actually working in.
        output_language:
          ((sop.generation_metadata as Record<string, unknown> | undefined)?.output_language as string | undefined) || locale,
        edited_with: 'manual_editor',
      },
      source_type: nextSourceType,
      overall_confidence: 1,
      needs_review: false,
    };
    if (cleaned.steps.length === 0) {
      setError(t('sop.atLeastOneStep'));
      return;
    }
    setIsSaving(true);
    setError(null);
    try {
      const payload = {
        sop: cleaned,
        folder_id: folderId || null,
        category: category || 'Uncategorized',
        tags: textToList(tags),
        visibility_scope: visibility,
        allowed_role_min: 'staff',
        source_type: nextSourceType,
        status: nextStatus,
        estimated_duration_minutes: estimatedDuration ? Number(estimatedDuration) : null,
      };
      const saved = initial
        ? await api.updateManagedSOP(initial.id, payload)
        : await api.createManagedSOP(payload);
      onSaved(nextStatus === 'published' && saved.status !== 'published'
        ? await api.publishManagedSOP(saved.id)
        : saved);
    } catch (err) {
      setError(err instanceof Error ? err.message : t('sop.couldNotSave'));
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <VStack align="stretch" gap="4">
      {error && <InlineAlert tone="red" title={t('sop.couldNotSaveTitle')}>{error}</InlineAlert>}
      <InlineAlert tone="blue" title={t('sopEditor.formIntroTitle')}>
        {t('sopEditor.formIntroBody')}
      </InlineAlert>
      <OpsPanel>
        <SectionHeader
          title={initial ? t('sopEditor.editSop') : t('sopEditor.createSopManually')}
          description={t('sopEditor.sectionDescription')}
          action={<StatusPill tone={status === 'published' ? 'green' : 'yellow'}>{status}</StatusPill>}
        />
        <VStack align="stretch" gap="3">
          <Box>
            <FieldLabel>{t('sopEditor.sopTitle')}</FieldLabel>
            <Input className="ops-input" value={sop.title} onChange={(event) => setSop({ ...sop, title: event.target.value })} placeholder={t('sopEditor.sopTitlePlaceholder')} />
          </Box>
          <Box>
            <FieldLabel>{t('sopEditor.overview')}</FieldLabel>
            <Textarea className="ops-input" value={sop.description} onChange={(event) => setSop({ ...sop, description: event.target.value })} placeholder={t('sopEditor.overviewPlaceholder')} rows={3} />
          </Box>
          <Flex gap="2" direction={{ base: 'column', md: 'row' }}>
            <Box flex="1">
              <FieldLabel>{t('sopEditor.category')}</FieldLabel>
              <Input className="ops-input" value={category} onChange={(event) => setCategory(event.target.value)} placeholder={t('sopEditor.categoryPlaceholder')} />
            </Box>
            <Box flex="1">
              <FieldLabel>{t('sopEditor.tags')}</FieldLabel>
              <Input className="ops-input" value={tags} onChange={(event) => setTags(event.target.value)} placeholder={t('sopEditor.tagsPlaceholder')} />
            </Box>
            <Box flex="1">
              <FieldLabel>{t('sopEditor.estimatedDuration')}</FieldLabel>
              <Input className="ops-input" type="number" value={estimatedDuration} onChange={(event) => setEstimatedDuration(event.target.value)} placeholder={t('sopEditor.minutesPlaceholder')} />
            </Box>
          </Flex>
          <Flex gap="2" direction={{ base: 'column', md: 'row' }}>
            <Box flex="1">
              <FieldLabel>{t('sopEditor.folder')}</FieldLabel>
              <select className="ops-select sop-form-select" value={folderId} onChange={(event) => setFolderId(event.target.value)}>
                <option value="">{t('sopEditor.noFolder')}</option>
                {folders.map((folder) => <option key={folder.id} value={folder.id}>{folder.name}</option>)}
              </select>
            </Box>
            <Box flex="1">
              <FieldLabel>{t('sopEditor.visibility')}</FieldLabel>
              <select className="ops-select sop-form-select" value={visibility} onChange={(event) => setVisibility(event.target.value)}>
                <option value="private">{t('sopEditor.visibilityPrivate')}</option>
                <option value="team">{t('sopEditor.visibilityTeam')}</option>
                <option value="organization">{t('sopEditor.visibilityOrg')}</option>
              </select>
            </Box>
          </Flex>
          <Box>
            <FieldLabel>{t('sopEditor.toolsMaterials')}</FieldLabel>
            <Textarea className="ops-input" value={listToText(sop.tools_materials)} onChange={(event) => setSop({ ...sop, tools_materials: textToList(event.target.value) })} placeholder={t('sopEditor.toolsMaterialsPlaceholder')} rows={2} />
          </Box>
          <Box>
            <FieldLabel>{t('sopEditor.safetyNotes')}</FieldLabel>
            <Textarea className="ops-input" value={listToText(sop.notes)} onChange={(event) => setSop({ ...sop, notes: textToList(event.target.value) })} placeholder={t('sopEditor.safetyNotesPlaceholder')} rows={2} />
          </Box>
        </VStack>
      </OpsPanel>

      <OpsPanel>
        <SectionHeader
          title={t('sopEditor.stepsHeader')}
          description={t('sopEditor.stepsCount').replace('{count}', String(sop.steps.length))}
          action={(
            <Button size="sm" variant="outline" onClick={addStep}>
              <Plus size={14} /> {t('sopEditor.addStep')}
            </Button>
          )}
        />
        <VStack align="stretch" gap="3">
          {sop.steps.map((step, idx) => (
            <Box key={idx} className="sop-editor-step">
              <Flex className="sop-editor-step-head" justify="space-between" align="center" gap="2">
                <Flex align="center" gap="3" minW="0">
                  <Box className="sop-editor-step-num">{idx + 1}</Box>
                  <Text fontWeight="800" color="gray.700">
                    {step.title || t('sopEditor.stepNumberLabel').replace('{n}', String(idx + 1))}
                  </Text>
                </Flex>
                <HStack gap="1" flexShrink="0">
                  <Button size="xs" variant="ghost" disabled={idx === 0} onClick={() => moveStep(idx, -1)} aria-label={t('sopEditor.up')}>
                    <ArrowUp size={14} />
                  </Button>
                  <Button size="xs" variant="ghost" disabled={idx === sop.steps.length - 1} onClick={() => moveStep(idx, 1)} aria-label={t('sopEditor.down')}>
                    <ArrowDown size={14} />
                  </Button>
                  <Button size="xs" variant="ghost" colorPalette="red" onClick={() => removeStep(idx)} aria-label={t('sopEditor.remove')}>
                    <Trash2 size={14} />
                  </Button>
                </HStack>
              </Flex>
              <VStack align="stretch" gap="3" className="sop-editor-step-body">
                <Box>
                  <FieldLabel>{t('sopEditor.stepTitleLabel')}</FieldLabel>
                  <Input className="ops-input" value={step.title} onChange={(event) => updateStep(idx, { title: event.target.value })} placeholder={t('sopEditor.stepTitlePlaceholder')} />
                </Box>
                <Box>
                  <FieldLabel>{t('sopEditor.instruction')}</FieldLabel>
                  <Textarea className="ops-input" value={step.description} onChange={(event) => updateStep(idx, { description: event.target.value })} placeholder={t('sopEditor.instructionPlaceholder')} rows={3} />
                </Box>
                <StepImagePicker
                  imageUrl={step.image_url}
                  onChange={(next) => updateStep(idx, { image_url: next ?? undefined })}
                  onUploadingChange={(isUploading) => {
                    setUploadingSteps((prev) => {
                      const next = new Set(prev);
                      if (isUploading) next.add(idx);
                      else next.delete(idx);
                      return next;
                    });
                  }}
                  label={t('sopEditor.stepPictureLabel')}
                  hint={t('sopEditor.stepPictureHint')}
                  captureLabel={t('sopEditor.stepPictureCapture')}
                  uploadLabel={t('sopEditor.stepPictureUpload')}
                  removeLabel={t('sopEditor.stepPictureRemove')}
                />
                <Flex gap="2" direction={{ base: 'column', md: 'row' }}>
                  <Box flex="1">
                    <FieldLabel>{t('sopEditor.stepTools')}</FieldLabel>
                    <Input className="ops-input" value={listToText(step.tools)} onChange={(event) => updateStep(idx, { tools: textToList(event.target.value) })} placeholder={t('sopEditor.commaSeparated')} />
                  </Box>
                  <Box flex="1">
                    <FieldLabel>{t('sopEditor.verificationChecks')}</FieldLabel>
                    <Input className="ops-input" value={listToText(step.checks)} onChange={(event) => updateStep(idx, { checks: textToList(event.target.value) })} placeholder={t('sopEditor.checksPlaceholder')} />
                  </Box>
                  <Box flex="1">
                    <FieldLabel>{t('sopEditor.stepDuration')}</FieldLabel>
                    <Input className="ops-input" type="number" value={step.estimated_time_minutes || ''} onChange={(event) => updateStep(idx, { estimated_time_minutes: event.target.value ? Number(event.target.value) : null })} placeholder={t('sopEditor.minutesPlaceholder')} />
                  </Box>
                </Flex>
                <Flex gap="2" direction={{ base: 'column', md: 'row' }}>
                  <Box flex="1">
                    <FieldLabel>{t('sopEditor.warning')}</FieldLabel>
                    <Input className="ops-input" value={step.warning || ''} onChange={(event) => updateStep(idx, { warning: event.target.value })} placeholder={t('sopEditor.warningPlaceholder')} />
                  </Box>
                  <Box flex="1">
                    <FieldLabel>{t('sopEditor.operatorNote')}</FieldLabel>
                    <Input className="ops-input" value={step.notes || ''} onChange={(event) => updateStep(idx, { notes: event.target.value })} placeholder={t('sopEditor.operatorNotePlaceholder')} />
                  </Box>
                </Flex>
              </VStack>
            </Box>
          ))}
        </VStack>
      </OpsPanel>

      <Box className="ops-sticky-actions">
        {anyUploading && (
          <Box mb="2">
            <InlineAlert tone="yellow" title={t('sopEditor.uploadInProgressTitle')}>
              {t('sopEditor.uploadInProgressBody')}
            </InlineAlert>
          </Box>
        )}
        <Flex justify="space-between" gap="2" align="center">
          <Button className="ops-touch" variant="outline" onClick={onCancel}>{t('sopEditor.cancel')}</Button>
          <HStack gap="2">
            <Button className="ops-touch" variant="outline" disabled={isSaving || anyUploading} onClick={() => { setStatus('draft'); void save('draft'); }}>{t('sopEditor.saveDraft')}</Button>
            <Button className="ops-touch" colorPalette="blue" disabled={isSaving || anyUploading} onClick={() => { setStatus('published'); void save('published'); }}>{t('sopEditor.publish')}</Button>
          </HStack>
        </Flex>
      </Box>
    </VStack>
  );
}

export default SOPEditor;
