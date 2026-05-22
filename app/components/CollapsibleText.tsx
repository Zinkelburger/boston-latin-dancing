'use client';

import { useState } from 'react';

type Props = {
  text: string;
  charLimit?: number;
  className?: string;
};

export default function CollapsibleText({ text, charLimit = 300, className = '' }: Props) {
  const [expanded, setExpanded] = useState(false);

  const isLong = text.length > charLimit;
  const visibleText = expanded || !isLong
    ? text
    : text.slice(0, text.lastIndexOf(' ', charLimit)) + '…';

  return (
    <div className={className}>
      {visibleText}
      {isLong && !expanded && (
        <button
          onClick={() => setExpanded(true)}
          className="block mt-1 text-xs font-medium text-rose-500 hover:text-rose-700 cursor-pointer"
        >
          Show more
        </button>
      )}
      {isLong && expanded && (
        <button
          onClick={() => setExpanded(false)}
          className="block mt-1 text-xs font-medium text-rose-500 hover:text-rose-700 cursor-pointer"
        >
          Show less
        </button>
      )}
    </div>
  );
}
