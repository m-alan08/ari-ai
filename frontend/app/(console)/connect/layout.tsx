import type { Metadata } from 'next';
import type { ReactNode } from 'react';
import { BRAND_NAME } from '@/components/brand';

export const metadata: Metadata = { title: `${BRAND_NAME} Console` };

export default function ConsoleAreaLayout({ children }: { children: ReactNode }) {
  return children;
}
