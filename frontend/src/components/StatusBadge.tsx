'use client';

import { Badge } from '@chakra-ui/react';
import type { VideoStatus } from '@/lib/api';

interface StatusBadgeProps {
  status: VideoStatus;
}

const statusConfig: Record<VideoStatus, { colorPalette: string; label: string }> = {
  uploaded: { colorPalette: 'blue', label: 'Uploaded' },
  transcribing: { colorPalette: 'yellow', label: 'Processing audio' },
  sop_generating: { colorPalette: 'purple', label: 'Building SOP' },
  completed: { colorPalette: 'green', label: 'Ready' },
  failed: { colorPalette: 'red', label: 'Needs retry' },
};

export function StatusBadge({ status }: StatusBadgeProps) {
  const config = statusConfig[status] || { colorPalette: 'gray', label: status };
  
  return (
    <Badge colorPalette={config.colorPalette} variant="subtle" px="2" py="1" borderRadius="full">
      {config.label}
    </Badge>
  );
}

export default StatusBadge;
