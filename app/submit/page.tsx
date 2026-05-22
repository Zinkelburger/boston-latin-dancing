import type { Metadata } from 'next';
import { SITE_URL } from '@/lib/constants';
import SubmitClient from './SubmitClient';

export const metadata: Metadata = {
  title: 'Submit an Event | Boston Latin Dance Map',
  description:
    'Submit your Latin dance event to be featured on the Boston Latin Dance Map. Organizers can request bachata, salsa, kizomba, zouk, and merengue events be added.',
  alternates: { canonical: `${SITE_URL}/submit` },
  openGraph: {
    title: 'Submit an Event | Boston Latin Dance Map',
    description:
      'Submit your Latin dance event to be featured on the Boston Latin Dance Map.',
    url: `${SITE_URL}/submit`,
  },
};

export default function SubmitPage() {
  return <SubmitClient />;
}
