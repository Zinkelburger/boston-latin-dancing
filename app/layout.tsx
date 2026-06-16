import '@/styles/globals.css';
import { ReactNode } from 'react';
import type { Metadata } from 'next';
import { SITE_URL } from '@/lib/constants';

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: {
    default: 'Boston Salsa Events | bostonsalsa.org',
    template: '%s | Boston Salsa Events',
  },
  description:
    'Salsa events in Boston. Find salsa and Latin dance socials and parties happening this week.',
  keywords: [
    'Boston salsa',
    'Boston salsa events',
    'salsa Boston',
    'salsa dancing Boston',
    'Boston bachata',
    'Boston latin dance',
    'where to dance salsa in Boston',
    'Boston dance events',
    'salsa classes Boston',
    'salsa dancing near me',
    'Latin dance socials Boston',
  ],
  openGraph: {
    title: 'Boston Salsa Events',
    description:
      'Find salsa and Latin dance events happening in Boston this week.',
    type: 'website',
    images: [{ url: '/icon.png', width: 512, height: 512 }],
  },
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className="h-full w-full">
      <body className="h-full w-full">{children}</body>
    </html>
  );
}
