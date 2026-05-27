import type { Metadata } from 'next';
import MapView from './components/MapView';

export const metadata: Metadata = {
  title: 'Boston Latin Dance Map',
  description:
    'Find salsa, bachata, kizomba, and zouk socials, classes, and events happening around Boston this week. Updated daily.',
  openGraph: {
    title: 'Boston Latin Dance Map',
    description:
      'Find salsa, bachata, kizomba, and zouk events happening around Boston this week.',
    type: 'website',
    images: [{ url: '/icon.png', width: 512, height: 512 }],
  },
};

export default function Home() {
  return (
    <div className="h-full w-full overflow-hidden">
      <MapView />
    </div>
  );
}
