'use client';

import { Box, Button, Flex, Heading, Spinner, Text, VStack, type BoxProps } from '@chakra-ui/react';
import type { ComponentType, KeyboardEvent, ReactNode } from 'react';
import type { Tone } from '@/lib/design';

export function PageShell({
  eyebrow,
  title,
  description,
  primaryAction,
  secondaryAction,
  children,
  maxW = '1180px',
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  primaryAction?: ReactNode;
  secondaryAction?: ReactNode;
  children: ReactNode;
  maxW?: string;
}) {
  return (
    <Box className="ops-page" maxW={maxW} mx="auto">
      <Flex className="ops-page-header" justify="space-between" align="end" gap="4">
        <Box minW="0">
          {eyebrow && <Text className="ops-eyebrow">{eyebrow}</Text>}
          <Heading as="h1" className="ops-title">{title}</Heading>
          {description && <Text className="ops-description">{description}</Text>}
        </Box>
        {(primaryAction || secondaryAction) && (
          <Flex className="ops-header-actions" gap="2">
            {secondaryAction}
            {primaryAction}
          </Flex>
        )}
      </Flex>
      {children}
    </Box>
  );
}

export function SectionHeader({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <Flex className="ops-section-header" justify="space-between" align="center" gap="3">
      <Box minW="0">
        <Heading as="h2" className="ops-section-title">{title}</Heading>
        {description && <Text className="ops-section-description">{description}</Text>}
      </Box>
      {action}
    </Flex>
  );
}

export function OpsPanel({
  children,
  padded = true,
  className = '',
  ...rest
}: BoxProps & {
  children: ReactNode;
  padded?: boolean;
  className?: string;
}) {
  return (
    <Box className={`ops-panel ${padded ? 'ops-panel-padded' : ''} ${className}`} {...rest}>
      {children}
    </Box>
  );
}

export function StatusPill({
  children,
  tone = 'neutral',
}: {
  children: ReactNode;
  tone?: Tone;
}) {
  return <Box className={`ops-pill ops-pill-${tone}`}>{children}</Box>;
}

export function InlineAlert({
  title,
  children,
  tone = 'neutral',
  action,
}: {
  title?: string;
  children: ReactNode;
  tone?: Tone;
  action?: ReactNode;
}) {
  return (
    <Flex className={`ops-alert ops-alert-${tone}`} justify="space-between" align="start" gap="3">
      <Box>
        {title && <Text className="ops-alert-title">{title}</Text>}
        <Text className="ops-alert-body">{children}</Text>
      </Box>
      {action}
    </Flex>
  );
}

export function EmptyAction({
  title,
  description,
  actionLabel,
  onAction,
}: {
  title: string;
  description: string;
  actionLabel?: string;
  onAction?: () => void;
}) {
  return (
    <VStack className="ops-empty" gap="3">
      <Box className="ops-empty-mark" />
      <Box textAlign="center">
        <Text className="ops-empty-title">{title}</Text>
        <Text className="ops-empty-text">{description}</Text>
      </Box>
      {actionLabel && onAction && (
        <Button className="ops-touch" colorPalette="blue" onClick={onAction}>
          {actionLabel}
        </Button>
      )}
    </VStack>
  );
}

export function QuickActionCard({
  icon: Icon,
  label,
  description,
  onClick,
  ariaLabel,
  tone = 'blue',
}: {
  icon: ComponentType<{ className?: string; size?: number | string }>;
  label: string;
  description?: string;
  onClick: () => void;
  ariaLabel?: string;
  tone?: Tone;
}) {
  const handleKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onClick();
    }
  };
  return (
    <div
      role="button"
      tabIndex={0}
      aria-label={ariaLabel || label}
      onClick={onClick}
      onKeyDown={handleKey}
      className="ops-quick-card group flex flex-col items-center gap-2 rounded-2xl border border-gray-200 bg-white px-4 py-5 text-center shadow-sm transition-all cursor-pointer hover:-translate-y-0.5 hover:shadow-md hover:border-blue-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400 focus-visible:ring-offset-2"
    >
      <span
        className={`ops-quick-card-icon ops-quick-card-icon-${tone} flex h-11 w-11 items-center justify-center rounded-full transition-colors`}
      >
        <Icon size={22} />
      </span>
      <span className="text-sm font-semibold text-gray-800 leading-tight">{label}</span>
      {description && (
        <span className="text-xs text-gray-500 leading-snug">{description}</span>
      )}
    </div>
  );
}

export function PageLoading({ label = 'Loading workspace' }: { label?: string }) {
  return (
    <Flex minH="54vh" align="center" justify="center">
      <VStack gap="3">
        <Spinner color="blue.500" />
        <Text color="gray.500" fontSize="sm">{label}</Text>
      </VStack>
    </Flex>
  );
}

export function ProgressLine({ value, tone = 'blue' }: { value: number; tone?: Tone }) {
  const safeValue = Math.max(0, Math.min(100, Number.isFinite(value) ? value : 0));
  return (
    <Box className="ops-progress">
      <Box className={`ops-progress-bar ops-progress-${tone}`} width={`${safeValue}%`} />
    </Box>
  );
}
