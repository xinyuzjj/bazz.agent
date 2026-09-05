import React, { createContext, useContext, useEffect, useState, useCallback } from "react";

export type Theme = "dark" | "light";

const STORAGE_KEY = "bazz.theme";

interface ThemeCtx {
  theme: Theme;
  setTheme: (t: Theme) => void;
  /** 一键切换 dark <-> light */
  toggle: () => void;
}

const Ctx = createContext<ThemeCtx | null>(null);

function loadInitial(): Theme {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    if (v === "dark" || v === "light") return v as Theme;
  } catch {}
  return "dark";
}

function persist(t: Theme) {
  try { localStorage.setItem(STORAGE_KEY, t); } catch {}
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = useState<Theme>(loadInitial);

  useEffect(() => {
    persist(theme);
    try {
      document.documentElement.setAttribute("data-theme", theme);
    } catch {}
  }, [theme]);

  const setTheme = useCallback((t: Theme) => setThemeState(t), []);
  const toggle = useCallback(() => setThemeState((p) => (p === "dark" ? "light" : "dark")), []);

  const value: ThemeCtx = { theme, setTheme, toggle };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useTheme(): ThemeCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("useTheme must be used within <ThemeProvider>");
  return c;
}