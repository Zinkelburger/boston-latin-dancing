import '@/styles/globals.css';
import { ReactNode } from 'react';
import type { Metadata } from 'next';

export const metadata: Metadata = {
  title: 'Boston Latin Dance Map – Salsa, Bachata, Kizomba & Zouk Events',
  description:
    'Find salsa, bachata, kizomba, zouk, and merengue events in Greater Boston. Interactive map of Latin dance socials, classes, and parties happening this week.',
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
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className="h-full overflow-hidden w-full">
      <body className="h-full overflow-hidden w-full">{children}</body>
    </html>
  );
}
