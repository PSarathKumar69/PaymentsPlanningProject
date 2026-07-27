import { api } from './client';

// payment_date/week are server-determined (log_payment()) — never client-supplied.
// note is optional free text Finance can attach (vendor detail modal's Pay flow).
export const postPayment = (vendorId: number, amount: number, note?: string) =>
  api.post<{ payment_status: string }>('/payments', { vendor_id: vendorId, amount, note: note || null });
