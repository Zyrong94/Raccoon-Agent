// Local theme state for the adapted home/demo shell.
// The official demo has no settings surface; this keeps the same .dark class
// switch it already used, and persists the choice for this browser only.
export type Theme = 'light' | 'dark';

export const THEME_STORAGE_KEY = 'raccoon-theme';

export function readStoredTheme(): Theme {
  if (typeof window === 'undefined') return 'light';
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    if (stored === 'dark' || stored === 'light') return stored;
  } catch {
    // Storage can be unavailable in private contexts; fall back to the DOM.
  }
  return document.documentElement.classList.contains('dark') ? 'dark' : 'light';
}

export function applyTheme(theme: Theme): void {
  document.documentElement.classList.toggle('dark', theme === 'dark');
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    // The class change still applies for this page lifetime.
  }
}

export function toggleTheme(): Theme {
  const next: Theme = document.documentElement.classList.contains('dark') ? 'light' : 'dark';
  applyTheme(next);
  return next;
}
