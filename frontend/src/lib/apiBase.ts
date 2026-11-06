// `??`, not `||`: an empty VITE_API_BASE_URL means "same origin" (relative /api URLs, served through the Vite proxy),
// while an unset variable keeps the old cross-origin default. Cookie sign-in requires same origin.
export const apiBaseUrl = (import.meta.env?.VITE_API_BASE_URL ?? 'http://localhost:8000').replace(/\/$/, '')
