'use client';

import { Box, Card, Flex, Heading, Text } from '@chakra-ui/react';
import { StatusBadge } from './StatusBadge';
import { useI18n } from '@/contexts/I18nContext';
import type { Video } from '@/lib/api';

interface VideoCardProps {
  video: Video;
  onSelect: (id: string) => void;
}

function VideoIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="24"
      height="24"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="m22 8-6 4 6 4V8Z" />
      <rect width="14" height="12" x="2" y="6" rx="2" ry="2" />
    </svg>
  );
}

function CheckIcon() {
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width="16"
      height="16"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <polyline points="20 6 9 17 4 12" />
    </svg>
  );
}

export function VideoCard({ video, onSelect }: VideoCardProps) {
  const { t, locale } = useI18n();
  const formattedDate = new Date(video.created_at).toLocaleDateString(locale === 'hi' ? 'hi-IN' : 'en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });

  return (
    <Card.Root
      cursor="pointer"
      onClick={() => onSelect(video.id)}
      _hover={{ shadow: 'md', transform: 'translateY(-2px)' }}
      transition="all 0.2s"
    >
      <Card.Body>
        <Flex gap="4" align="start">
          <Box color="gray.500" flexShrink={0}>
            <VideoIcon />
          </Box>
          <Box flex="1" minW="0">
            <Flex justify="space-between" align="start" mb="2">
              <Heading as="h3" size="md" overflow="hidden" textOverflow="ellipsis" whiteSpace="nowrap">
                {video.title || video.filename}
              </Heading>
              <StatusBadge status={video.status} />
            </Flex>
            <Text fontSize="sm" color="gray.500" mb="2">
              {video.filename}
            </Text>
            <Flex gap="4" fontSize="sm" color="gray.600">
              <Text>{formattedDate}</Text>
              {video.has_transcript && (
                <Flex align="center" gap="1" color="green.600">
                  <CheckIcon />
                  <Text>{t('videos.transcript')}</Text>
                </Flex>
              )}
              {video.has_sop && (
                <Flex align="center" gap="1" color="green.600">
                  <CheckIcon />
                  <Text>{t('videos.sop')}</Text>
                </Flex>
              )}
            </Flex>
          </Box>
        </Flex>
      </Card.Body>
    </Card.Root>
  );
}

export default VideoCard;
