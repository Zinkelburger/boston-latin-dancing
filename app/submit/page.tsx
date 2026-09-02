import type { Metadata } from 'next';
import { SITE_URL } from '@/lib/constants';
import SubmitClient from './SubmitClient';

export const metadata: Metadata = {
  title: 'Submit an Event',
  description:
    'Submit your Latin dance event to be featured on Boston Salsa Events. Organizers can add salsa, bachata, kizomba, zouk, and merengue events.',
  alternates: { canonical: `${SITE_URL}/submit` },
  openGraph: {
    title: 'Submit an Event',
    description: 'Submit your Latin dance event to be featured on the Boston Latin Dance Map.',
    url: `${SITE_URL}/submit`,
  },
};

export default function SubmitPage() {
  return <SubmitClient />;
}
