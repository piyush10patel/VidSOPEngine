'use client';

import { Box, Button, Flex, Heading, Text, VStack } from '@chakra-ui/react';
import type { ReactNode } from 'react';

/**
 * Standard empty state — used everywhere a list/page can be empty.
 *
 * Per docs/ux.md:
 *   icon → headline → helper → primary CTA → optional secondary
 *
 * Empty states ARE the onboarding. Don't add a separate tutorial.
 */
export interface EmptyStateProps {
  icon?: ReactNode;
  headline: string;
  helper?: string;
  cta?: {
    label: string;
    onClick: () => void;
  };
  secondary?: {
    label: string;
    onClick: () => void;
  };
}

export function EmptyState({
  icon,
  headline,
  helper,
  cta,
  secondary,
}: EmptyStateProps) {
  return (
    <Box
      w="full"
      borderRadius="lg"
      border="1px dashed"
      borderColor="gray.300"
      bg="gray.50"
      p={{ base: '6', md: '10' }}
      textAlign="center"
    >
      <VStack gap="3" maxW="md" mx="auto">
        {icon && (
          <Flex
            w="14"
            h="14"
            align="center"
            justify="center"
            borderRadius="full"
            bg="blue.50"
            color="blue.600"
          >
            {icon}
          </Flex>
        )}
        <Heading as="h3" size="md" color="gray.900">
          {headline}
        </Heading>
        {helper && (
          <Text fontSize="sm" color="gray.600" lineHeight="1.5">
            {helper}
          </Text>
        )}
        {cta && (
          <Button
            mt="2"
            size="md"
            colorPalette="blue"
            onClick={cta.onClick}
            minH="44px"
          >
            {cta.label}
          </Button>
        )}
        {secondary && (
          <Button
            variant="ghost"
            size="sm"
            onClick={secondary.onClick}
            color="gray.600"
          >
            {secondary.label}
          </Button>
        )}
      </VStack>
    </Box>
  );
}

export default EmptyState;
