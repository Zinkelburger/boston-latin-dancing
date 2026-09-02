import { describe, expect, it } from 'vitest';
import { offerFromCost, parsePrice } from '@/lib/offer';

describe('parsePrice', () => {
  it('reads a leading dollar amount', () => {
    expect(parsePrice('$10')).toBe(10);
    expect(parsePrice('10')).toBe(10);
    expect(parsePrice('$22.50')).toBe(22.5);
  });

  it('takes the lower bound of a range', () => {
    expect(parsePrice('$10-15')).toBe(10);
    expect(parsePrice('$10 – $15')).toBe(10);
  });

  it('finds the number after a label or on a later line', () => {
    expect(parsePrice('Cover: $15')).toBe(15);
    expect(parsePrice('From $20')).toBe(20);
    expect(parsePrice('Cost:\n$15')).toBe(15);
  });

  it('treats free and empty as zero', () => {
    expect(parsePrice('Free')).toBe(0);
    expect(parsePrice('free')).toBe(0);
    expect(parsePrice('FREE entry')).toBe(0);
    expect(parsePrice('')).toBe(0);
    expect(parsePrice('   ')).toBe(0);
  });

  it('returns null when no number is present', () => {
    expect(parsePrice('Cost')).toBeNull();
    expect(parsePrice('Cover:')).toBeNull();
    expect(parsePrice('donation')).toBeNull();
  });
});

describe('offerFromCost', () => {
  it('emits a numeric schema.org Offer for a plain price', () => {
    expect(offerFromCost('$10')).toEqual({
      '@type': 'Offer',
      price: 10,
      priceCurrency: 'USD',
      availability: 'https://schema.org/InStock',
    });
  });

  it('never emits a string or undefined price', () => {
    for (const cost of ['$10', '10', '$10-15', 'Free', '', '$15 at the door']) {
      const offer = offerFromCost(cost);
      expect(offer).toBeDefined();
      expect(typeof offer?.price).toBe('number');
    }
  });

  it('prices free events at zero without a description', () => {
    expect(offerFromCost('Free')).toMatchObject({ price: 0 });
    expect(offerFromCost('Free')).not.toHaveProperty('description');
    expect(offerFromCost('')).toMatchObject({ price: 0 });
  });

  it('keeps the raw text as description when it says more than the number', () => {
    expect(offerFromCost('$15 at the door')).toMatchObject({
      price: 15,
      description: '$15 at the door',
    });
    expect(offerFromCost('$10-15')).toMatchObject({ price: 10, description: '$10-15' });
    expect(offerFromCost('$10')).not.toHaveProperty('description');
    expect(offerFromCost('10')).not.toHaveProperty('description');
  });

  it('adds the url only when one is given', () => {
    expect(offerFromCost('$10', 'https://example.com/tickets')).toMatchObject({
      url: 'https://example.com/tickets',
    });
    expect(offerFromCost('$10', null)).not.toHaveProperty('url');
    expect(offerFromCost('$10', '')).not.toHaveProperty('url');
  });

  it('returns undefined for missing or unparseable costs', () => {
    expect(offerFromCost(undefined)).toBeUndefined();
    expect(offerFromCost(null)).toBeUndefined();
    expect(offerFromCost('Cost')).toBeUndefined();
  });
});
