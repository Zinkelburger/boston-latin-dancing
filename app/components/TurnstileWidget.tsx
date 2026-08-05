'use client';

import { useEffect, useRef } from 'react';

export const TURNSTILE_SITE_KEY = '0x4AAAAAAEGpyLaFBK7uF126';
const SCRIPT_SRC = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';

declare global {
  interface Window {
    turnstile?: {
      render: (el: HTMLElement, opts: Record<string, unknown>) => string;
      reset: (id?: string) => void;
      remove: (id: string) => void;
    };
  }
}

function ensureScript() {
  if (document.querySelector(`script[src="${SCRIPT_SRC}"]`)) return;
  const script = document.createElement('script');
  script.src = SCRIPT_SRC;
  script.async = true;
  script.defer = true;
  document.head.appendChild(script);
}

/**
 * Cloudflare Turnstile widget. Calls onToken with the current token, and
 * with '' when the token expires. Tokens are single-use: after a failed
 * submit, call window.turnstile?.reset() so the retry gets a fresh one.
 */
export default function TurnstileWidget({ onToken }: { onToken: (token: string) => void }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const widgetIdRef = useRef<string | null>(null);
  const onTokenRef = useRef(onToken);
  onTokenRef.current = onToken;

  useEffect(() => {
    ensureScript();
    let cancelled = false;

    const tryRender = () => {
      if (cancelled || widgetIdRef.current !== null) return;
      if (window.turnstile && containerRef.current) {
        widgetIdRef.current = window.turnstile.render(containerRef.current, {
          sitekey: TURNSTILE_SITE_KEY,
          action: 'turnstile-spin-v2',
          callback: (token: string) => onTokenRef.current(token),
          'expired-callback': () => onTokenRef.current(''),
        });
      } else {
        setTimeout(tryRender, 100);
      }
    };
    tryRender();

    return () => {
      cancelled = true;
      if (widgetIdRef.current !== null) {
        window.turnstile?.remove(widgetIdRef.current);
        widgetIdRef.current = null;
      }
    };
  }, []);

  return (
    <div
      ref={containerRef}
      className="cf-turnstile"
      data-sitekey={TURNSTILE_SITE_KEY}
      data-action="turnstile-spin-v2"
    />
  );
}
