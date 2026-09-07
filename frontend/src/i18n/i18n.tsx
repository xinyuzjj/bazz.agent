import React, { createContext, useContext, useEffect, useState, useCallback } from "react";
import { zh, en, Dict } from "./locales";

export type Locale = "zh" | "en";

const STORAGE_KEY = "bazz.locale";

interface I18nCtx {
  locale: Locale;
  setLocale: (l: Locale) => void;
  /** 一键切换 zh <-> en */
  toggle: () => void;
  /** 取出字典里的翻译；未命中则原样返回 key；支持 @{name} 占位 */
  t: (key: string, params?: Record<string, string | number>) => string;
}

const Ctx = createContext<I18nCtx | null>(null);

function loadInitial(): Locale {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    if (v === "zh" || v === "en") return v;
  } catch {}
  // 默认中文（用户明确要求先中文化，再做英文）
  return "zh";
}

function persist(l: Locale) {
  try { localStorage.setItem(STORAGE_KEY, l); } catch {}
}

/** 模块级读当前 locale（供 api 层 / 非 React 代码使用） */
export function getLocale(): Locale {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    if (v === "zh" || v === "en") return v;
  } catch {}
  return "zh";
}

export function I18nProvider({ children }: { children: React.ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(loadInitial);

  useEffect(() => {
    persist(locale);
    try {
      document.documentElement.setAttribute("lang", locale === "zh" ? "zh-CN" : "en");
      document.documentElement.setAttribute("data-locale", locale);
    } catch {}
  }, [locale]);

  const setLocale = useCallback((l: Locale) => setLocaleState(l), []);
  const toggle = useCallback(() => setLocaleState((p) => (p === "zh" ? "en" : "zh")), []);

  const t = useCallback((key: string, params?: Record<string, string | number>) => {
    const dict: Dict = locale === "zh" ? zh : en;
    let s = dict[key] ?? key;
    if (params) {
      for (const k of Object.keys(params)) {
        // 同时兼容 "@{k}" 与 "{k}" 两种占位符写法
        s = s.split(`@{${k}}`).join(String(params[k]));
        s = s.split(`{${k}}`).join(String(params[k]));
      }
    }
    return s;
  }, [locale]);

  const value: I18nCtx = { locale, setLocale, toggle, t };
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useI18n(): I18nCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error("useI18n must be used within <I18nProvider>");
  return c;
}

/** 便捷 hook：直接拿 t() */
export function useT() {
  return useI18n().t;
}