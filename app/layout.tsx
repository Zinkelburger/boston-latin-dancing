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
    'Where to dance salsa and bachata in Boston — socials, classes, and parties on one live map.',
  // No og:image on purpose: without one, WhatsApp/iMessage render a compact
  // text-only preview instead of a big embed card.
  openGraph: {
    title: 'Boston Salsa Events',
    description:
      'Where to dance salsa and bachata in Boston — socials, classes, and parties on one live map.',
    type: 'website',
  },
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className="h-full w-full">
      <body className="h-full w-full">{children}</body>
    </html>
  );
}
