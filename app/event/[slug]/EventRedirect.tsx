'use client';

import { useEffect } from 'react';

export default function EventRedirect({ to }: { to: string }) {
  useEffect(() => {
    window.location.replace(to);
  }, [to]);

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <div className="text-center text-gray-400 text-sm">
        Loading map…
      </div>
    </div>
  );
}
