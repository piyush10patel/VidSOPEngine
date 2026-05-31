'use client';

import { useEffect, useMemo, useState } from 'react';
import type { ComponentType } from 'react';
import { Box, Button, Flex, Text, VStack } from '@chakra-ui/react';
import { usePathname, useRouter } from 'next/navigation';
import {
  BookOpen,
  ChevronLeft,
  ChevronRight,
  LogOut,
  Menu,
  Sparkles,
  Upload,
  X,
} from 'lucide-react';
import { useAuth } from '@/contexts/AuthContext';
import { useI18n } from '@/contexts/I18nContext';
import { LanguageSwitcher } from '@/components/LanguageSwitcher';

type IconCmp = ComponentType<{ className?: string; size?: number | string; strokeWidth?: number | string }>;

type NavItem = {
  label: string;
  short: string;
  href: string;
  description: string;
  Icon: IconCmp;
};

export function Navbar() {
  const { user, logout } = useAuth();
  const { t } = useI18n();
  const router = useRouter();
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);

  useEffect(() => {
    const saved = localStorage.getItem('vidsopengine.sidebarCollapsed');
    setCollapsed(saved === 'true');
  }, []);

  useEffect(() => {
    document.body.classList.toggle('app-shell-authenticated', Boolean(user));
    document.body.classList.toggle('sidebar-collapsed', Boolean(user && collapsed));
    return () => {
      document.body.classList.remove('app-shell-authenticated');
      document.body.classList.remove('sidebar-collapsed');
    };
  }, [collapsed, user]);

  const role = (user?.role || 'staff').toLowerCase();
  const canUpload = role === 'admin' || role === 'manager' || role === 'superadmin';

  const navItems = useMemo<NavItem[]>(() => {
    const items: NavItem[] = [
      { label: t('nav.sops'), short: t('nav.shortSops'), href: '/sops', description: t('nav.sopsHint'), Icon: BookOpen },
    ];
    if (canUpload) {
      items.push({ label: t('nav.sopAi'), short: t('nav.shortAi'), href: '/upload', description: t('nav.uploadHint'), Icon: Sparkles });
    }
    return items;
  }, [canUpload, t]);

  const handleLogout = () => {
    logout();
    router.replace('/login');
  };

  const toggleCollapsed = () => {
    const next = !collapsed;
    setCollapsed(next);
    localStorage.setItem('vidsopengine.sidebarCollapsed', String(next));
  };

  const go = (href: string) => {
    setMobileOpen(false);
    router.push(href);
  };

  const isActive = (href: string) => pathname === href || (href !== '/' && pathname.startsWith(href));

  if (!user) return null;

  return (
    <>
      <Flex
        className="app-mobile-topbar"
        display={{ base: 'flex', md: 'none' }}
        position="fixed"
        top="0"
        left="0"
        right="0"
        h="56px"
        zIndex="120"
        bg="white"
        borderBottom="1px solid"
        borderColor="gray.200"
        align="center"
        justify="space-between"
        px="3"
      >
        <Button className="ops-touch" size="sm" variant="ghost" onClick={() => setMobileOpen(true)} aria-label={t('nav.menu')} flexShrink={0}>
          <Menu size={20} />
        </Button>
        <Box flex="1" minW="0" display="flex" alignItems="center" justifyContent="center" px="2">
          <Text fontWeight="700" color="gray.800">VidSOPEngine</Text>
        </Box>
        <Box flexShrink={0} />
      </Flex>

      {mobileOpen && (
        <Box display={{ base: 'block', md: 'none' }} position="fixed" inset="0" zIndex="130" bg="blackAlpha.400" onClick={() => setMobileOpen(false)}>
          <Box className="app-mobile-drawer" w="86vw" maxW="340px" h="full" p="4" onClick={(event) => event.stopPropagation()} bg="white">
            <Flex justify="space-between" align="center" mb="5">
              <Box>
                <Text fontWeight="700" fontSize="lg" color="gray.800">VidSOPEngine</Text>
                <Text fontSize="xs" color="gray.500" mt="1">{user.email}</Text>
              </Box>
              <Button size="sm" variant="ghost" onClick={() => setMobileOpen(false)} aria-label={t('nav.close')}>
                <X size={18} />
              </Button>
            </Flex>
            <VStack align="stretch" gap="1">
              {navItems.map((item) => {
                const Icon = item.Icon;
                return (
                  <Button
                    key={item.href}
                    className="ops-touch"
                    justifyContent="flex-start"
                    variant={isActive(item.href) ? 'subtle' : 'ghost'}
                    colorPalette={isActive(item.href) ? 'blue' : 'gray'}
                    onClick={() => go(item.href)}
                  >
                    <Flex direction="column" align="start" gap="0">
                      <Flex align="center" gap="2">
                        <Box as="span" className="app-nav-icon"><Icon size={16} /></Box>
                        <Text>{item.label}</Text>
                      </Flex>
                      <Text fontSize="xs" color="gray.500">{item.description}</Text>
                    </Flex>
                  </Button>
                );
              })}
              <Box pt="3"><LanguageSwitcher /></Box>
              <Button mt="4" className="ops-touch" variant="outline" onClick={handleLogout}>
                <LogOut size={16} /> {t('common.signOut')}
              </Button>
            </VStack>
          </Box>
        </Box>
      )}

      <Box
        as="nav"
        className="app-sidebar"
        display={{ base: 'none', md: 'block' }}
        position="fixed"
        top="0"
        left="0"
        bottom="0"
        w={collapsed ? '72px' : '232px'}
        bg="white"
        borderRight="1px solid"
        borderColor="gray.200"
        zIndex="110"
        p="3"
      >
        <Flex direction="column" h="full">
          <Flex justify={collapsed ? 'center' : 'space-between'} align="center" mb="7">
            {collapsed ? (
              <Text fontWeight="800" color="blue.600">V</Text>
            ) : (
              <Text fontWeight="700" fontSize="lg" color="gray.800">VidSOPEngine</Text>
            )}
            <Button className="app-sidebar-toggle" size="xs" variant="ghost" onClick={toggleCollapsed} aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}>
              {collapsed ? <ChevronRight size={14} /> : <ChevronLeft size={14} />}
            </Button>
          </Flex>
          <VStack align="stretch" gap="1">
            {navItems.map((item) => {
              const active = isActive(item.href);
              const Icon = item.Icon;
              return (
                <Button
                  key={item.href}
                  className={`ops-touch app-nav-button ${active ? 'app-nav-button-active' : ''}`}
                  justifyContent={collapsed ? 'center' : 'flex-start'}
                  variant={active ? 'subtle' : 'ghost'}
                  colorPalette={active ? 'blue' : 'gray'}
                  onClick={() => router.push(item.href)}
                  aria-label={item.label}
                >
                  {collapsed ? (
                    <Box as="span" className="app-nav-icon"><Icon size={16} /></Box>
                  ) : (
                    <Flex align="center" gap="2">
                      <Box as="span" className="app-nav-icon"><Icon size={16} /></Box>
                      <Text>{item.label}</Text>
                    </Flex>
                  )}
                </Button>
              );
            })}
          </VStack>
          <Box mt="auto">
            {!collapsed && (
              <Box mb="3" p="3" border="1px solid" borderColor="gray.200" borderRadius="8px" bg="gray.50">
                <Text fontSize="xs" color="gray.500">{t('nav.signedIn')} - {role}</Text>
                <LanguageSwitcher compact />
                <Text fontSize="sm" color="gray.800" lineClamp={1}>{user.email}</Text>
              </Box>
            )}
            <Button size="sm" className="ops-touch" w="full" variant="outline" colorPalette="gray" onClick={handleLogout} aria-label={t('common.signOut')}>
              {collapsed ? <LogOut size={16} /> : <Flex align="center" gap="2"><LogOut size={14} /> {t('common.signOut')}</Flex>}
            </Button>
          </Box>
        </Flex>
      </Box>

      <Box className="ops-mobile-bottom-nav" display={{ base: 'grid', md: 'none' }}>
        {navItems.slice(0, 1).map((item) => {
          const Icon = item.Icon;
          const active = isActive(item.href);
          return (
            <button
              key={item.href}
              className={`ops-bottom-nav-item ${active ? 'ops-bottom-nav-item-active' : ''}`}
              onClick={() => router.push(item.href)}
              aria-label={item.label}
              aria-current={active ? 'page' : undefined}
            >
              <Icon size={20} strokeWidth={active ? 2.4 : 2} />
              <span className="ops-bottom-nav-label">{item.short}</span>
            </button>
          );
        })}
        {canUpload && (
          <button
            className={`ops-bottom-nav-ai ${isActive('/upload') ? 'ops-bottom-nav-ai-active' : ''}`}
            onClick={() => router.push('/upload')}
            aria-label={t('nav.sopAi')}
            aria-current={isActive('/upload') ? 'page' : undefined}
          >
            <span className="ops-bottom-nav-ai-orb">
              <Sparkles size={22} strokeWidth={2.4} />
            </span>
            <span className="ops-bottom-nav-ai-label">{t('nav.sopAi')}</span>
          </button>
        )}
        <button
          className="ops-bottom-nav-item"
          onClick={handleLogout}
          aria-label={t('common.signOut')}
        >
          <LogOut size={20} />
          <span className="ops-bottom-nav-label">{t('common.signOut')}</span>
        </button>
      </Box>
    </>
  );
}
