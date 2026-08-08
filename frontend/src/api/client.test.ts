import { describe, expect, it } from 'vitest';
import { parseContentDispositionFilename } from './client';

// Bug fix: any real download filename here has spaces ("Vendor Payment
// Plan - Aug-2026 - ...xlsx"), which makes Starlette's FileResponse switch
// to the RFC 5987 extended form (`filename*=utf-8''<percent-encoded>`)
// instead of a plain `filename="..."` — the old regex only matched the
// plain form and silently returned null for every one of these, forever.

describe('parseContentDispositionFilename', () => {
  it('parses the plain form (dataset 1: no special characters)', () => {
    const header = 'attachment; filename="audit-log.csv"';
    expect(parseContentDispositionFilename(header)).toBe('audit-log.csv');
  });

  it('parses the RFC 5987 extended form with spaces (dataset 1: Vendor Payment Plan export)', () => {
    const header =
      "attachment; filename*=utf-8''Vendor%20Payment%20Plan%20-%20Aug-2026%20-%2006-Aug-2026%20143022.xlsx";
    expect(parseContentDispositionFilename(header)).toBe(
      'Vendor Payment Plan - Aug-2026 - 06-Aug-2026 143022.xlsx'
    );
  });

  it('parses the RFC 5987 extended form with spaces (dataset 2: Min Funds Verification export, different name/timestamp)', () => {
    const header =
      "attachment; filename*=utf-8''Min%20Funds%20Verification%20-%20Sep-2026%20-%2012-Sep-2026%20091530.xlsx";
    expect(parseContentDispositionFilename(header)).toBe(
      'Min Funds Verification - Sep-2026 - 12-Sep-2026 091530.xlsx'
    );
  });

  it('returns null for a missing header', () => {
    expect(parseContentDispositionFilename(null)).toBeNull();
  });

  it('returns null for an empty header', () => {
    expect(parseContentDispositionFilename('')).toBeNull();
  });
});
