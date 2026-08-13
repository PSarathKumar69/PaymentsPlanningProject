import { api } from './client';
import { Payment, Vendor, VendorAging, VendorCategoryOption, VendorPaymentTracking } from '../types';

export const listVendors = () => api.get<Vendor[]>('/vendors');
export const getVendor = (id: number) => api.get<Vendor>(`/vendors/${id}`);
export const getVendorAging = (id: number) => api.get<VendorAging>(`/vendors/${id}/aging`);

// Bulk form — one request instead of one per vendor (was firing ~83
// simultaneous GET /vendors/{id}/aging calls on every Planning page load).
export const getAllVendorsAging = () => api.get<Array<VendorAging & { vendor_id: number }>>('/vendors/aging');
export const getVendorPaymentTracking = () => api.get<VendorPaymentTracking[]>('/vendors/payment-tracking');

// Response shape mirrors backend/api/routers/vendors.py's patch_vendor() —
// sibling_field/sibling_old_value/sibling_new_value are present when this
// edit auto-derived its sibling (category<->priority_tag, backend/
// configuration/vendor_edits.py::_derive_sibling()) server-side in the same
// call; commitment_months_warning is the non-blocking "commitment_months
// still unconfirmed" flag. Callers that only need old/new can ignore the rest.
export interface VendorPatchResponse {
  old_value: unknown;
  new_value: unknown;
  sibling_field?: string;
  sibling_old_value?: unknown;
  sibling_new_value?: unknown;
  commitment_months_warning?: string;
}

export const patchVendor = (id: number, field: string, new_value: unknown) =>
  api.patch<VendorPatchResponse>(`/vendors/${id}`, { field, new_value });

export const getVendorPayments = (id: number) => api.get<Payment[]>(`/vendors/${id}/payments`);

// Read-only source of truth for the category dropdown(s) (P2 demo-polish
// task, CLAUDE.md rule 7) — the category SET itself is a fixed backend enum,
// not Finance-editable data, but the label/value pairs still come from here
// rather than a hardcoded second copy (constants/enums.ts's CATEGORY_OPTIONS/
// CATEGORY_LABEL are kept only as a same-session bootstrap default now).
export const getVendorCategories = () => api.get<VendorCategoryOption[]>('/vendors/categories');
