import React from "react";
// 内联线性 SVG 图标 — 无字体依赖，无 emoji
type P = { className?: string; size?: number };
const base = (p: P) => ({ width: p.size ?? 16, height: p.size ?? 16, className: p.className });
export const I = {
  Hex: (p: P = {}) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" {...base(p)}>
      <path d="M12 2 L21 7 V17 L12 22 L3 17 V7 Z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  ),
  Chat: (p: P = {}) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" {...base(p)}>
      <path d="M4 6h16v10H8l-4 4z" />
    </svg>
  ),
  Market: (p: P = {}) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" {...base(p)}>
      <path d="M3 17l5-5 4 3 8-9" /><path d="M14 6h7v7" />
    </svg>
  ),
  Wallet: (p: P = {}) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" {...base(p)}>
      <rect x="3" y="6" width="18" height="13" rx="2" /><path d="M16 12h4" /><circle cx="17" cy="12" r="1" fill="currentColor" />
    </svg>
  ),
  Qr: (p: P = {}) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" {...base(p)}>
      <rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" />
      <rect x="3" y="14" width="7" height="7" rx="1" />
      <path d="M14 14h3v3h-3z M19 14h2 M21 18v3 M14 19v2 M18 21h3" />
    </svg>
  ),
  Key: (p: P = {}) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" {...base(p)}>
      <circle cx="8" cy="8" r="4" /><path d="M11 11 20 20 M16 16l2.5-2.5 M14 18l2-2" />
    </svg>
  ),
  Cex: (p: P = {}) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" {...base(p)}>
      <rect x="3" y="4" width="18" height="16" rx="1" />
      <path d="M7 9h10 M7 13h6 M7 17h8" />
    </svg>
  ),
  Council: (p: P = {}) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" {...base(p)}>
      <circle cx="7" cy="8" r="2" /><circle cx="17" cy="8" r="2" /><circle cx="12" cy="16" r="2" />
      <path d="M9 9l3 5 M15 9l-3 5" />
    </svg>
  ),
  Gear: (p: P = {}) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" {...base(p)}>
      <circle cx="12" cy="12" r="3" />
      <path d="M19 12a7 7 0 0 0-.2-1.6l2-1.5-2-3.4-2.4.8a7 7 0 0 0-2.8-1.6L13 2h-2l-.6 2.7a7 7 0 0 0-2.8 1.6l-2.4-.8-2 3.4 2 1.5A7 7 0 0 0 5 12c0 .6.1 1.1.2 1.6l-2 1.5 2 3.4 2.4-.8a7 7 0 0 0 2.8 1.6L11 22h2l.6-2.7a7 7 0 0 0 2.8-1.6l2.4.8 2-3.4-2-1.5c.1-.5.2-1 .2-1.6z" />
    </svg>
  ),
  Memory: (p: P = {}) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" {...base(p)}>
      <circle cx="12" cy="12" r="9" /><path d="M12 3v9l6 3" />
    </svg>
  ),
  Search: (p: P = {}) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" {...base(p)}>
      <circle cx="11" cy="11" r="6" /><path d="M20 20l-3.5-3.5" />
    </svg>
  ),
  Plus: (p: P = {}) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" {...base(p)}>
      <path d="M12 5v14 M5 12h14" />
    </svg>
  ),
  Send: (p: P = {}) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" {...base(p)}>
      <path d="M3 11l18-8-8 18-2-8z" />
    </svg>
  ),
  Mic: (p: P = {}) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" {...base(p)}>
      <rect x="9" y="3" width="6" height="12" rx="3" />
      <path d="M5 11a7 7 0 0 0 14 0 M12 18v3" />
    </svg>
  ),
  Star: (p: P = {}) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" {...base(p)}>
      <path d="M12 3l3 6 6.5 1-4.7 4.6 1.1 6.4L12 18l-5.9 3 1.1-6.4L2.5 10 9 9z" />
    </svg>
  ),
  Check: (p: P = {}) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" {...base(p)}>
      <path d="M5 12l5 5 9-11" />
    </svg>
  ),
  X: (p: P = {}) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" {...base(p)}>
      <path d="M6 6l12 12 M18 6l-12 12" />
    </svg>
  ),
  Minus: (p: P = {}) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" {...base(p)}>
      <path d="M5 12h14" />
    </svg>
  ),
  Arrow: (p: P = {}) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" {...base(p)}>
      <path d="M5 12h14 M13 5l7 7-7 7" />
    </svg>
  ),
  Copy: (p: P = {}) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" {...base(p)}>
      <rect x="8" y="8" width="12" height="12" rx="1" /><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3" />
    </svg>
  ),
  Download: (p: P = {}) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" {...base(p)}>
      <path d="M12 3v12 M7 10l5 5 5-5 M5 21h14" />
    </svg>
  ),
  Refresh: (p: P = {}) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" {...base(p)}>
      <path d="M3 12a9 9 0 0 1 15-6.7L21 8 M21 3v5h-5 M21 12a9 9 0 0 1-15 6.7L3 16 M3 21v-5h5" />
    </svg>
  ),
  Shield: (p: P = {}) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" {...base(p)}>
      <path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z" />
    </svg>
  ),
  Zap: (p: P = {}) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" {...base(p)}>
      <path d="M13 3L4 14h6l-1 7 9-11h-6z" />
    </svg>
  ),
  Lock: (p: P = {}) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" {...base(p)}>
      <rect x="4" y="11" width="16" height="10" rx="1" />
      <path d="M8 11V7a4 4 0 0 1 8 0v4" />
    </svg>
  ),
  Flame: (p: P = {}) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" {...base(p)}>
      <path d="M12 3s5 5 5 10a5 5 0 0 1-10 0c0-2 1-3 2-4-1 4 3 4 3 0 0-3-2-4 0-6z" />
    </svg>
  ),
  Target: (p: P = {}) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" {...base(p)}>
      <circle cx="12" cy="12" r="8" />
      <circle cx="12" cy="12" r="4" />
      <circle cx="12" cy="12" r="0.5" fill="currentColor" />
    </svg>
  ),
  Link: (p: P = {}) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" {...base(p)}>
      <path d="M9 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1" />
      <path d="M15 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1" />
    </svg>
  ),
  Pin: (p: P = {}) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" {...base(p)}>
      <path d="M9 3h6v4l-2 2v8h-2V9L9 7z" /><path d="M7 21h10" />
    </svg>
  ),
  Trash: (p: P = {}) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" {...base(p)}>
      <path d="M4 7h16 M9 7V4h6v3 M6 7l1 14h10l1-14" />
    </svg>
  ),
  Bolt: (p: P = {}) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" {...base(p)}>
      <path d="M13 3L4 14h6l-1 7 9-11h-6z" />
    </svg>
  ),
  Cpu: (p: P = {}) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" {...base(p)}>
      <rect x="6" y="6" width="12" height="12" rx="1" />
      <rect x="9" y="9" width="6" height="6" rx="0.5" />
      <path d="M9 3v3 M15 3v3 M9 18v3 M15 18v3 M3 9h3 M3 15h3 M18 9h3 M18 15h3" />
    </svg>
  ),
  Alert: (p: P = {}) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" {...base(p)}>
      <path d="M12 4l9 16H3z" />
      <path d="M12 10v5" /><circle cx="12" cy="17.6" r="0.6" fill="currentColor" />
    </svg>
  ),
  Stop: (p: P = {}) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" {...base(p)}>
      <rect x="5" y="5" width="14" height="14" rx="1.5" />
    </svg>
  ),
  Play: (p: P = {}) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" {...base(p)}>
      <path d="M7 4l13 8-13 8z" />
    </svg>
  ),
  Grid: (p: P = {}) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" {...base(p)}>
      <rect x="4" y="4" width="7" height="7" rx="1" />
      <rect x="13" y="4" width="7" height="7" rx="1" />
      <rect x="4" y="13" width="7" height="7" rx="1" />
      <rect x="13" y="13" width="7" height="7" rx="1" />
    </svg>
  ),
  Plug: (p: P = {}) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" {...base(p)}>
      <path d="M9 3v4 M15 3v4 M7 7h10v3a5 5 0 0 1-10 0z" />
      <path d="M12 15v6" />
    </svg>
  ),
  Users: (p: P = {}) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" {...base(p)}>
      <circle cx="9" cy="8" r="3.2" />
      <path d="M3.5 19c.6-3.2 2.6-5 5.5-5s4.9 1.8 5.5 5" />
      <circle cx="17" cy="9" r="2.6" />
      <path d="M16 14.4c2.3.3 3.9 1.8 4.4 4.1" />
    </svg>
  ),
  Megaphone: (p: P = {}) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...base(p)}>
      <path d="M3 10v4l11 4V6z" />
      <path d="M14 7l4-2v14l-4-2" />
      <path d="M7 14v3a2 2 0 0 0 4 0v-1.6" />
    </svg>
  ),
  Sun: (p: P = {}) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...base(p)}>
      <circle cx="12" cy="12" r="4.2" />
      <path d="M12 3v2 M12 19v2 M3 12h2 M19 12h2 M5.6 5.6l1.4 1.4 M17 17l1.4 1.4 M5.6 18.4l1.4-1.4 M17 7l1.4-1.4" />
    </svg>
  ),
  Moon: (p: P = {}) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...base(p)}>
      <path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z" />
    </svg>
  ),
  Globe: (p: P = {}) => (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" {...base(p)}>
      <circle cx="12" cy="12" r="9" />
      <path d="M3 12h18 M12 3a13.5 13.5 0 0 1 0 18 M12 3a13.5 13.5 0 0 0 0 18" />
    </svg>
  ),
};