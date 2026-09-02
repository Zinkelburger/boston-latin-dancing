'use client';

import { useState, useCallback } from 'react';
import Icon from './Icons';

type Props = {
  url: string;
  title: string;
  text?: string;
  className?: string;
};

export default function ShareButton({ url, title, text, className = '' }: Props) {
  const [copied, setCopied] = useState(false);

  const handleShare = useCallback(async () => {
    if (navigator.share) {
      try {
        await navigator.share({ title, text, url });
        return;
      } catch {
        // User cancelled or share failed — fall through to clipboard
      }
    }

    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      prompt('Copy this link:', url);
    }
  }, [url, title, text]);

  return (
    <button
      type="button"
      onClick={handleShare}
      className={`pretty-pill pretty-pill-neutral ${className}`}
      aria-label="Share event"
      title="Share"
    >
      {copied ? (
        <>
          <Icon name="check" size={14} /> Copied!
        </>
      ) : (
        <>
          <Icon name="share" size={14} /> Share
        </>
      )}
    </button>
  );
}
