/**
 * schema.org Offer for an event's free-text `cost` ("$10", "Free", "$15 at
 * the door", "$10-15"). Google's rich-result validator rejects an Offer whose
 * `price` is missing or a non-numeric string, so one is only emitted when the
 * cost parses to a number.
 */
export type SchemaOffer = {
  '@type': 'Offer';
  price: number;
  priceCurrency: 'USD';
  availability: 'https://schema.org/InStock';
  url?: string;
  /** The raw cost text, kept so "$20 at the door" is not flattened to "20". */
  description?: string;
};

const FREE_RE = /\bfree\b/i;
// Leading price: an optional "$", then a number with up to two decimals. Ranges
// ("$10-15", "$10 – $15") resolve to their lower bound, the price of admission.
const PRICE_RE = /\$?\s*(\d+(?:\.\d{1,2})?)/;

/** Numeric price for a cost string, or null when it does not name one. */
export function parsePrice(cost: string): number | null {
  const text = cost.trim();
  if (text === '' || FREE_RE.test(text)) return 0;
  const m = PRICE_RE.exec(text);
  if (!m) return null;
  const price = Number(m[1]);
  return Number.isFinite(price) ? price : null;
}

export function offerFromCost(
  cost: string | null | undefined,
  url?: string | null,
): SchemaOffer | undefined {
  if (cost == null) return undefined;
  const price = parsePrice(cost);
  if (price === null) return undefined;
  const offer: SchemaOffer = {
    '@type': 'Offer',
    price,
    priceCurrency: 'USD',
    availability: 'https://schema.org/InStock',
  };
  if (url) offer.url = url;
  const text = cost.trim();
  if (text && price !== 0 && text !== String(price) && text !== `$${price}`) {
    offer.description = text;
  }
  return offer;
}
