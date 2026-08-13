// Login-credential task: the auth calls the frontend ever makes. Session
// identity lives in an httpOnly cookie the backend sets/clears itself
// (backend/api/routers/auth.py) — nothing here ever touches a token
// directly, same reasoning api/client.ts's request() already relies on for
// every other same-origin call.
import { api, ApiError } from './client';

export interface CurrentUser {
  username: string;
  display_name: string;
  role: string;
}

export function login(username: string, password: string, remember: boolean): Promise<CurrentUser> {
  return api.post<CurrentUser>('/auth/login', { username, password, remember });
}

export function logout(): Promise<{ ok: boolean }> {
  return api.post<{ ok: boolean }>('/auth/logout');
}

// Configuration-page self-service password change (replaces the old
// "Forgot Password?" link, which never did more than show a message) —
// proves identity with the CURRENT password, same as the backend route
// requires (backend/api/routers/auth.py's change_password()).
export function changePassword(currentPassword: string, newPassword: string): Promise<{ ok: boolean }> {
  return api.put<{ ok: boolean }>('/auth/change-password', {
    current_password: currentPassword,
    new_password: newPassword,
  });
}

// Distinguishes "confirmed not logged in" (401 -> null) from "couldn't
// reach the backend at all" (network/other error -> rethrow) — App.tsx's
// startup check needs to show the login page for the former but shouldn't
// silently do the same for a transient network hiccup.
export async function fetchCurrentUser(): Promise<CurrentUser | null> {
  try {
    return await api.get<CurrentUser>('/auth/me');
  } catch (e) {
    if (e instanceof ApiError) return null;
    throw e;
  }
}
