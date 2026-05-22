'use client';

import ShareButton from '@/app/components/ShareButton';

type Props = {
  url: string;
  title: string;
  text: string;
};

export default function EventDetailClient({ url, title, text }: Props) {
  return <ShareButton url={url} title={title} text={text} />;
}
