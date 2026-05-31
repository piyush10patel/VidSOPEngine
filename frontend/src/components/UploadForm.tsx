'use client';

import { useRef, useState, useCallback } from 'react';
import { Box, Button, Flex, Input, Progress, Text, VStack } from '@chakra-ui/react';
import { Camera, Loader2, Sparkles, Upload, Video as VideoIcon } from 'lucide-react';
import { useI18n } from '@/contexts/I18nContext';
import { ApiError, api, type Video } from '@/lib/api';

interface UploadFormProps {
  onUploadComplete: (video: Video) => void;
}

const ALLOWED_EXTENSIONS = ['.mp4', '.mov', '.avi', '.mkv'];
const MAX_FILE_SIZE = 500 * 1024 * 1024; // 500MB

// Hardcoded: every video uploaded through this flow is a physical
// process recording, and the pipeline picks granularity automatically
// at SOP-time. Both knobs were UI noise — operators don't have the
// context to make these choices, and the backend's auto classifier
// matches their picks ~95% of the time.
const VIDEO_TYPE = 'physical' as const;
const PIPELINE_COMPLEXITY = 'auto' as const;

type Stage = 'idle' | 'uploading' | 'starting' | 'done';

export function UploadForm({ onUploadComplete }: UploadFormProps) {
  const { t } = useI18n();
  const captureRef = useRef<HTMLInputElement | null>(null);
  const libraryRef = useRef<HTMLInputElement | null>(null);
  const [stage, setStage] = useState<Stage>('idle');
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [quotaHit, setQuotaHit] = useState(false);
  const [title, setTitle] = useState('');

  const validateFile = (file: File): string | null => {
    const extension = '.' + (file.name.split('.').pop()?.toLowerCase() || '');
    if (!ALLOWED_EXTENSIONS.includes(extension)) {
      return t('upload.invalidFormat').replace('{exts}', ALLOWED_EXTENSIONS.join(', '));
    }
    if (file.size > MAX_FILE_SIZE) {
      return t('upload.tooLarge').replace('{mb}', String(MAX_FILE_SIZE / (1024 * 1024)));
    }
    return null;
  };

  const handleUpload = useCallback(
    async (file: File | null | undefined) => {
      if (!file) return;
      const validationError = validateFile(file);
      if (validationError) {
        setError(validationError);
        return;
      }
      setError(null);
      setProgress(0);
      setStage('uploading');
      try {
        const video = await api.uploadVideo(
          file,
          title || undefined,
          VIDEO_TYPE,
          (p) => setProgress(p),
          PIPELINE_COMPLEXITY,
        );
        setStage('starting');
        // Kick off the pipeline immediately so the user lands on the
        // processing page with work already in flight. We swallow errors
        // here because the /videos/[id] page can still kick it off — we
        // only want to skip the extra click on the happy path.
        try {
          await api.runPipeline(video.id);
        } catch {
          // Page can recover; non-fatal.
        }
        setStage('done');
        setTitle('');
        onUploadComplete(video);
      } catch (err) {
        if (err instanceof ApiError) {
          setError(err.message);
          // 402 = backend's quota signal. Show an inline upgrade card
          // instead of treating it like a generic upload failure.
          if (err.statusCode === 402) setQuotaHit(true);
        } else {
          setError(t('upload.uploadFailed'));
        }
        setStage('idle');
        setProgress(0);
      }
    },
    [onUploadComplete, t, title],
  );

  const busy = stage !== 'idle';
  const stageLabel =
    stage === 'uploading'
      ? t('upload.uploading')
      : stage === 'starting'
      ? t('upload.startingPipeline')
      : stage === 'done'
      ? t('upload.openingSop')
      : '';

  return (
    <VStack gap="4" align="stretch" w="full">
      <Input
        className="ops-input"
        placeholder={t('upload.videoTitlePlaceholder')}
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        disabled={busy}
      />

      <Flex direction={{ base: 'column', md: 'row' }} gap="3">
        <input
          ref={captureRef}
          type="file"
          accept="video/*"
          capture="environment"
          style={{ display: 'none' }}
          onChange={(e) => handleUpload(e.target.files?.[0])}
        />
        <input
          ref={libraryRef}
          type="file"
          accept="video/*"
          style={{ display: 'none' }}
          onChange={(e) => handleUpload(e.target.files?.[0])}
        />

        <button
          type="button"
          disabled={busy}
          onClick={() => captureRef.current?.click()}
          className="sop-ai-choice sop-ai-choice-primary"
        >
          <span className="sop-ai-choice-icon">
            <Camera size={28} />
          </span>
          <span className="sop-ai-choice-label">{t('upload.captureWithCamera')}</span>
          <span className="sop-ai-choice-sub">{t('upload.captureWithCameraSub')}</span>
        </button>

        <button
          type="button"
          disabled={busy}
          onClick={() => libraryRef.current?.click()}
          className="sop-ai-choice"
        >
          <span className="sop-ai-choice-icon sop-ai-choice-icon-alt">
            <Upload size={28} />
          </span>
          <span className="sop-ai-choice-label">{t('upload.uploadFromDevice')}</span>
          <span className="sop-ai-choice-sub">{t('upload.uploadFromDeviceSub')}</span>
        </button>
      </Flex>

      {busy && (
        <Box className="sop-ai-progress">
          <Flex align="center" gap="3" mb="2">
            {stage === 'uploading' ? <VideoIcon size={16} /> : stage === 'starting' ? <Loader2 size={16} className="sop-ai-spin" /> : <Sparkles size={16} />}
            <Text fontWeight="700" color="gray.700" fontSize="sm">{stageLabel}</Text>
          </Flex>
          {stage === 'uploading' && (
            <>
              <Progress.Root value={progress} size="sm">
                <Progress.Track>
                  <Progress.Range />
                </Progress.Track>
              </Progress.Root>
              <Text fontSize="xs" color="gray.500" mt="1">{progress}%</Text>
            </>
          )}
        </Box>
      )}

      {error && !quotaHit && (
        <Box p="3" bg="red.50" borderRadius="md" border="1px solid" borderColor="red.200">
          <Text color="red.600" fontSize="sm">{error}</Text>
        </Box>
      )}

      {quotaHit && (
        <Box p="4" borderRadius="12px" border="1px solid" borderColor="orange.200" bg="orange.50">
          <Flex align="flex-start" gap="3" wrap="wrap">
            <Sparkles size={20} />
            <Box flex="1">
              <Text fontWeight="800" color="orange.900">{t('upload.quotaTitle')}</Text>
              <Text fontSize="sm" color="orange.800" mt="1">{error}</Text>
            </Box>
          </Flex>
        </Box>
      )}

      <Text fontSize="xs" color="gray.500">
        {t('upload.supportedFormats')}
      </Text>

      <Button variant="ghost" size="sm" onClick={() => {}} display="none">
        <Sparkles size={14} />
      </Button>
    </VStack>
  );
}

export default UploadForm;
