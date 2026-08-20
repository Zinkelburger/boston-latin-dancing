'use client';

import { useEffect, useRef } from 'react';

export const TURNSTILE_SITE_KEY = '0x4AAAAAAEHE4t36ytfkThN8';
export const TURNSTILE_ACTION = 'turnstile-spin-v2';
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

function ensureScript(onError: () => void) {
  const existing = document.querySelector<HTMLScriptElement>(`script[src="${SCRIPT_SRC}"]`);
  if (existing) {
    existing.addEventListener('error', onError);
    return;
  }
  const script = document.createElement('script');
  script.src = SCRIPT_SRC;
  script.async = true;
  script.defer = true;
  script.addEventListener('error', onError);
  document.head.appendChild(script);
}

// A blocked or failed challenge never calls back, so the submit button would
// stay disabled with nothing on screen explaining why. Give up waiting for the
// script after this long and let the form say so instead.
const RENDER_TIMEOUT_MS = 15000;

/**
 * Cloudflare Turnstile widget. Calls onToken with the current token, and
 * with '' when the token expires. Tokens are single-use: after a submit
 * attempt, call window.turnstile.reset(widgetId) so a retry gets a fresh one.
 *
 * Calls onUnavailable if the challenge cannot run at all — script blocked, or
 * Cloudflare erroring — so the form can explain the dead end rather than
 * leaving the submit button greyed out forever.
 */
export default function TurnstileWidget({
  onToken,
  onWidgetId,
  onUnavailable,
}: {
  onToken: (token: string) => void;
  onWidgetId?: (id: string) => void;
  onUnavailable?: () => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const widgetIdRef = useRef<string | null>(null);
  const onTokenRef = useRef(onToken);
  const onWidgetIdRef = useRef(onWidgetId);
  const onUnavailableRef = useRef(onUnavailable);
  onTokenRef.current = onToken;
  onWidgetIdRef.current = onWidgetId;
  onUnavailableRef.current = onUnavailable;

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const fail = () => {
      if (cancelled) return;
      cancelled = true;
      clearTimeout(timer);
      onUnavailableRef.current?.();
    };

    ensureScript(fail);
    const deadline = Date.now() + RENDER_TIMEOUT_MS;

    const tryRender = () => {
      if (cancelled || widgetIdRef.current !== null) return;
      if (window.turnstile && containerRef.current) {
        widgetIdRef.current = window.turnstile.render(containerRef.current, {
          sitekey: TURNSTILE_SITE_KEY,
          action: TURNSTILE_ACTION,
          callback: (token: string) => onTokenRef.current(token),
          'expired-callback': () => onTokenRef.current(''),
          'error-callback': () => fail(),
        });
        if (widgetIdRef.current) onWidgetIdRef.current?.(widgetIdRef.current);
      } else if (Date.now() >= deadline) {
        fail();
      } else {
        timer = setTimeout(tryRender, 100);
      }
    };
    tryRender();

    return () => {
      cancelled = true;
      clearTimeout(timer);
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
      data-action={TURNSTILE_ACTION}
    />
  );
}
