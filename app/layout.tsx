import '@/styles/globals.css';
import { ReactNode } from 'react';
import type { Metadata } from 'next';

export const metadata: Metadata = {
  title: 'Boston Latin Dance Map',
  description:
    'A map of Latin dance events happening around Boston this week.',
  keywords: [
    'Boston salsa',
    'Boston bachata',
    'Boston latin dance',
    'latin dancing Boston',
    'where to dance in Boston',
    'Boston salsa map',
    'Boston dance events',
    'Boston kizomba',
    'Boston zouk',
    'salsa dancing near me',
    'bachata Boston',
    'Latin dance socials Boston',
  ],
  openGraph: {
    title: 'Boston Latin Dance Map',
    description:
      'A map of Latin dance events happening around Boston this week.',
    type: 'website',
    images: [{ url: '/icon.png', width: 512, height: 512 }],
  },
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className="h-full overflow-hidden w-full">
      <body className="h-full overflow-hidden w-full">{children}</body>
    </html>
  );
}
