import { api } from './client';
import { Vendor, VendorAging, VendorPaymentTracking } from '../types';

export const listVendors = () => api.get<Vendor[]>('/vendors');
export const getVendor = (id: number) => api.get<Vendor>(`/vendors/${id}`);
export const getVendorAging = (id: number) => api.get<VendorAging>(`/vendors/${id}/aging`);

// Bulk form — one request instead of one per vendor (was firing ~83
// simultaneous GET /vendors/{id}/aging calls on every Planning page load).
export const getAllVendorsAging = () => api.get<Array<VendorAging & { vendor_id: number }>>('/vendors/aging');
export const getVendorPaymentTracking = () => api.get<VendorPaymentTracking[]>('/vendors/payment-tracking');

export const patchVendor = (id: number, field: string, new_value: unknown) =>
  api.patch<{ old_value: unknown; new_value: unknown }>(`/vendors/${id}`, { field, new_value });

// model: 1 (default) — New Model 2's own tab uses finalizeNewModel2() instead
// (POST /models/5/finalize), which calls this same underlying snapshot with
// model=5 server-side; this wrapper is only for the generic route's own default.
export const finalizePlan = (model = 1) =>
  api.post<{ vendor_count: number }>('/vendors/finalize-plan', { model });
