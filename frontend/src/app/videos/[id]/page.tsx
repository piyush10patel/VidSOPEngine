'use client';

import { useEffect, useState, useCallback } from 'react';
import { useParams, useRouter, useSearchParams } from 'next/navigation';
import {
  Box,
  Container,
  Heading,
  Text,
  VStack,
  Button,
  Flex,
  Spinner,
  Card,
} from '@chakra-ui/react';
import { StatusBadge } from '@/components/StatusBadge';
import { SOPViewer } from '@/components/SOPViewer';
import { api, type Video, type SOP, type SOPResponse, ApiError } from '@/lib/api';
import { useAuthGuard } from '@/contexts/AuthContext';
import { useI18n } from '@/contexts/I18nContext';

const POLL_INTERVAL = 3000;

function BackIcon() {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <path d="m12 19-7-7 7-7" />
      <path d="M19 12H5" />
    </svg>
  );
}

function PlayIcon() {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <polygon points="5 3 19 12 5 21 5 3" />
    </svg>
  );
}

export default function VideoDetailPage() {
  const { user, isLoading: authLoading } = useAuthGuard();
  const { t, locale } = useI18n();
  const params = useParams();
  const router = useRouter();
  const searchParams = useSearchParams();
  const videoId = params.id as string;
  const autoMode = searchParams?.get('auto') === '1';

  const [video, setVideo] = useState<Video | null>(null);
  const [sop, setSop] = useState<SOPResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isPipelineRunning, setIsPipelineRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pipelineError, setPipelineError] = useState<string | null>(null);

  const fetchVideo = useCallback(async () => {
    try {
      const data = await api.getVideo(videoId);
      setVideo(data);
      setError(null);
      if (data.has_sop) {
        try {
          const sopData = await api.getSOP(videoId);
          setSop(sopData);
        } catch {
          // SOP not available yet
        }
      }
    } catch (err) {
      if (err instanceof ApiError && err.statusCode === 404) {
        setError(t('videos.videoNotFound'));
      } else {
        setError(t('videos.failedLoadVideo'));
      }
    } finally {
      setIsLoading(false);
    }
  }, [videoId, t]);

  useEffect(() => {
    fetchVideo();
  }, [fetchVideo]);

  useEffect(() => {
    if (!video) return;
    const shouldPoll =
      (isPipelineRunning || video.status === 'transcribing' || video.status === 'sop_generating') &&
      !video.has_sop;
    if (!shouldPoll) return;
    const interval = setInterval(fetchVideo, POLL_INTERVAL);
    return () => clearInterval(interval);
  }, [video, isPipelineRunning, fetchVideo]);

  const handleRunPipeline = useCallback(async () => {
    if (!video) return;
    const previousVideo = video;
    const optimisticStatus =
      video.video_type === 'ui' || video.has_transcript ? 'sop_generating' : 'transcribing';
    setIsPipelineRunning(true);
    setPipelineError(null);
    setVideo({ ...video, status: optimisticStatus });
    try {
      await api.runPipeline(videoId);
      await fetchVideo();
    } catch (err) {
      setPipelineError(err instanceof ApiError ? err.message : t('videos.pipelineFailed'));
      setVideo(previousVideo);
    } finally {
      setIsPipelineRunning(false);
    }
  }, [fetchVideo, t, video, videoId]);

  // Auto mode: kick off the pipeline if upload didn't already.
  useEffect(() => {
    if (!autoMode || !video || isPipelineRunning) return;
    if (video.status !== 'uploaded' && video.status !== 'failed') return;
    handleRunPipeline();
  }, [autoMode, video, isPipelineRunning, handleRunPipeline]);

  // Auto mode: redirect to managed SOP once it's ready.
  useEffect(() => {
    if (!autoMode || !sop) return;
    router.replace(`/sops/${sop.id}`);
  }, [autoMode, sop, router]);

  const handleBackClick = () => {
    router.push('/sops');
  };

  const formattedDate = video
    ? new Date(video.created_at).toLocaleDateString(locale === 'hi' ? 'hi-IN' : 'en-US', {
        year: 'numeric',
        month: 'long',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
      })
    : '';

  const canRunPipeline =
    video && (video.status === 'uploaded' || video.status === 'failed') && !isPipelineRunning;

  const isProcessing =
    video &&
    (video.status === 'transcribing' ||
      (video.status === 'sop_generating' && !video.has_sop));

  if (authLoading || !user || isLoading) {
    return (
      <Container maxW="container.xl" py="8">
        <Flex justify="center" py="12">
          <Spinner size="xl" color="blue.500" />
        </Flex>
      </Container>
    );
  }

  if (error) {
    return (
      <Container maxW="container.xl" py="8">
        <VStack gap="4">
          <Box p="6" bg="red.50" borderRadius="md" border="1px solid" borderColor="red.200">
            <Text color="red.600">{error}</Text>
          </Box>
          <Button onClick={handleBackClick}>{t('videos.backToVideosLabel')}</Button>
        </VStack>
      </Container>
    );
  }

  return (
    <Container maxW="container.xl" py="8">
      <VStack gap="6" align="stretch">
        <Flex align="center" gap="4">
          <Button variant="ghost" onClick={handleBackClick} p="2">
            <BackIcon />
          </Button>
          <Box flex="1">
            <Flex align="center" gap="3" mb="1" wrap="wrap">
              <Heading as="h1" size="xl">{video?.title || video?.filename}</Heading>
              {video && <StatusBadge status={video.status} />}
              {video?.video_type && (
                <Box
                  px="2" py="0.5"
                  borderRadius="md"
                  bg={video.video_type === 'ui' ? 'purple.100' : 'orange.100'}
                  color={video.video_type === 'ui' ? 'purple.800' : 'orange.800'}
                  fontSize="xs" fontWeight="semibold"
                >
                  {video.video_type === 'ui' ? t('videos.uiWorkflowBadge') : t('videos.physicalProcessBadge')}
                </Box>
              )}
            </Flex>
            <Text color="gray.600">{video?.filename}</Text>
          </Box>
        </Flex>

        <Card.Root>
          <Card.Body>
            <VStack align="stretch" gap="4">
              <Heading as="h2" size="md">{t('videos.videoDetailsHeading')}</Heading>
              <Flex gap="8" wrap="wrap">
                <Box>
                  <Text fontSize="sm" color="gray.500">{t('videos.uploaded')}</Text>
                  <Text fontWeight="medium">{formattedDate}</Text>
                </Box>
                <Box>
                  <Text fontSize="sm" color="gray.500">{t('videos.status')}</Text>
                  <Text fontWeight="medium" textTransform="capitalize">{video?.status.replace('_', ' ')}</Text>
                </Box>
                <Box>
                  <Text fontSize="sm" color="gray.500">{t('videos.transcript')}</Text>
                  <Text fontWeight="medium">{video?.has_transcript ? t('videos.available') : t('videos.notGenerated')}</Text>
                </Box>
                <Box>
                  <Text fontSize="sm" color="gray.500">{t('videos.sop')}</Text>
                  <Text fontWeight="medium">{video?.has_sop ? t('videos.available') : t('videos.notGenerated')}</Text>
                </Box>
              </Flex>
            </VStack>
          </Card.Body>
        </Card.Root>

        <Card.Root>
          <Card.Body>
            <VStack align="stretch" gap="4">
              <Heading as="h2" size="md">{t('videos.aiPipelineHeading')}</Heading>
              <Text color="gray.600">{t('videos.aiPipelineBody')}</Text>

              {isProcessing && (
                <Flex align="center" gap="3" p="4" bg="blue.50" borderRadius="md">
                  <Spinner size="sm" color="blue.500" />
                  <Text color="blue.700">
                    {video?.status === 'transcribing' ? t('videos.transcribingVideo') : t('videos.generatingSop')}
                  </Text>
                </Flex>
              )}

              {pipelineError && (
                <Box p="3" bg="red.50" borderRadius="md" border="1px solid" borderColor="red.200">
                  <Text color="red.600" fontSize="sm">{pipelineError}</Text>
                </Box>
              )}

              <Button colorPalette="blue" onClick={handleRunPipeline} disabled={!canRunPipeline || isPipelineRunning}>
                {isPipelineRunning ? (
                  <>
                    <Spinner size="sm" />
                    {t('videos.runningEllipsis')}
                  </>
                ) : (
                  <>
                    <PlayIcon />
                    {video?.status === 'failed' ? t('videos.retryPipelineLabel') : t('videos.runPipelineLabel')}
                  </>
                )}
              </Button>
            </VStack>
          </Card.Body>
        </Card.Root>

        {video?.has_sop && sop && (
          <Card.Root>
            <Card.Body>
              <VStack align="stretch" gap="4">
                <Flex justify="space-between" align="center">
                  <Heading as="h2" size="md">{t('videos.generatedSopHeading')}</Heading>
                  <Text fontSize="sm" color="gray.500">
                    {t('videos.generatedOn').replace('{date}', new Date(sop.created_at).toLocaleDateString(locale === 'hi' ? 'hi-IN' : 'en-US'))}
                  </Text>
                </Flex>
                <SOPViewer
                  sop={sop.sop}
                  videoId={videoId}
                  editable={true}
                  canViewInternal={Boolean(sop.can_view_internal)}
                  isFinalized={sop.is_finalized}
                  onSave={async (updated: SOP) => {
                    const next = await api.updateSOP(videoId, updated);
                    setSop(next);
                  }}
                  onFinalize={async (updated: SOP) => {
                    const next = await api.finalizeSOP(videoId, updated);
                    setSop(next);
                  }}
                />
              </VStack>
            </Card.Body>
          </Card.Root>
        )}

        {video?.has_sop && !sop && (
          <Card.Root>
            <Card.Body>
              <Flex justify="center" align="center" py="8">
                <Spinner size="md" color="blue.500" />
                <Text ml="3" color="gray.600">{t('videos.loadingSop')}</Text>
              </Flex>
            </Card.Body>
          </Card.Root>
        )}
      </VStack>
    </Container>
  );
}
