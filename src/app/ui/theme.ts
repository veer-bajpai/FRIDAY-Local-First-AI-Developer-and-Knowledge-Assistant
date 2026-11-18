"use client";

import { useCallback, useSyncExternalStore } from "react";

export type ThemeMode = "system" | "light" | "dark";
const KEY = "friday-theme";
const listeners = new Set<() => void>();

function readMode(): ThemeMode {
  try {
    const value = window.localStorage.getItem(KEY);
    return value === "light" || value === "dark" ? value : "system";
  } catch {
    return "system";
  }
}

function systemDark() {
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

export function applyTheme(mode: ThemeMode = readMode()) {
  const dark = mode === "dark" || (mode === "system" && systemDark());
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  document.documentElement.style.colorScheme = dark ? "dark" : "light";
}

function subscribe(callback: () => void) {
  listeners.add(callback);
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  const onSystem = () => {
    if (readMode() === "system") applyTheme("system");
    callback();
  };
  media.addEventListener("change", onSystem);
  window.addEventListener("storage", onSystem);
  return () => {
    listeners.delete(callback);
    media.removeEventListener("change", onSystem);
    window.removeEventListener("storage", onSystem);
  };
}

function snapshot() {
  const mode = readMode();
  const dark = mode === "dark" || (mode === "system" && systemDark());
  return `${mode}:${dark ? "dark" : "light"}`;
}

/** Theme follows the OS until the user picks light or dark; the choice is remembered. */
export function useTheme() {
  const value = useSyncExternalStore(subscribe, snapshot, () => "system:light");
  const [mode, resolved] = value.split(":") as [ThemeMode, "light" | "dark"];

  const setMode = useCallback((next: ThemeMode) => {
    try {
      if (next === "system") window.localStorage.removeItem(KEY);
      else window.localStorage.setItem(KEY, next);
    } catch {
      /* storage unavailable: still apply for this session */
    }
    applyTheme(next);
    listeners.forEach((listener) => listener());
  }, []);

  const toggle = useCallback(() => setMode(resolved === "dark" ? "light" : "dark"), [resolved, setMode]);
  return { mode, resolved, setMode, toggle };
}

/** Runs before first paint (inlined in layout) so the page never flashes the wrong theme. */
export const THEME_BOOTSTRAP = `(function(){try{var m=localStorage.getItem('${KEY}');var d=m==='dark'||(m!=='light'&&matchMedia('(prefers-color-scheme: dark)').matches);var e=document.documentElement;e.dataset.theme=d?'dark':'light';e.style.colorScheme=d?'dark':'light';}catch(_){}})();`;
