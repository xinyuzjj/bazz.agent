// 全局美化弹窗（替代原生 window.confirm / Electron dialog.showMessageBox）
// 设计系统：glass-bright 深色卡 + 金色主按钮 + 红色危险按钮，带淡入缩放动画。
// 用法：
//   import { confirmDialog, showAppDialog } from "../components/ConfirmDialog";
//   if (await confirmDialog(t("chat.confirmDelSession"), { danger: true })) { ... }
//   const r = await showAppDialog({ title, detail, choices: [{id:"tray",label},...], checkbox: {label} });
import React, { useEffect, useRef, useState } from "react";
import { getLocale } from "../i18n/i18n";
import { zh, en } from "../i18n/locales";

/** 非 React 环境取当前语言文案（弹窗 API 在事件回调里调用，不走 hook） */
const tr = (key: string) => (getLocale() === "en" ? en[key] : zh[key]) ?? key;

export type DialogChoice = { id: string; label: string; variant?: "gold" | "ghost" | "danger" };
export type DialogOptions = {
  title: string;
  detail?: string;
  icon?: "info" | "danger";
  choices: DialogChoice[];          // 1~3 个；row 布局时建议 2 个
  layout?: "row" | "list";          // row=横排（确认/取消），list=竖排（多选项）
  checkbox?: { label: string; defaultChecked?: boolean };  // 可选「记住我的选择」
  cancelId?: string;                // Esc / 点遮罩时返回的 choice id（默认最后一个 ghost）
};

type PendingDialog = {
  opts: DialogOptions;
  resolve: (r: { choice: string; checked: boolean }) => void;
};

let push: ((d: PendingDialog) => void) | null = null;
let seq = 1;

function open(opts: DialogOptions): Promise<{ choice: string; checked: boolean }> {
  return new Promise((resolve) => {
    if (push) push({ opts, resolve });
    else resolve({ choice: opts.cancelId ?? opts.choices[opts.choices.length - 1]?.id ?? "", checked: false });
  });
}

/** window.confirm 的直接替代：确定→true，取消/Esc→false */
export function confirmDialog(message: string, o?: { title?: string; confirmText?: string; cancelText?: string; danger?: boolean }): Promise<boolean> {
  // 传了 title 时 message 不能丢：标题走 title，具体问句下移到 detail 行渲染。
  // （此前只有一个 title 字段，带上 title 的调用点会把 "确定删除…？" 整句吞掉，
  //   弹窗退化成只有「删除」两个字的标题。）
  const title = o?.title ?? message;
  return open({
    title,
    detail: title === message ? undefined : message,
    icon: o?.danger ? "danger" : "info",
    choices: o?.danger
      ? [{ id: "cancel", label: o?.cancelText ?? tr("dialog.cancel"), variant: "ghost" }, { id: "ok", label: o?.confirmText ?? tr("dialog.delete"), variant: "danger" }]
      : [{ id: "cancel", label: o?.cancelText ?? tr("dialog.cancel"), variant: "ghost" }, { id: "ok", label: o?.confirmText ?? tr("dialog.ok"), variant: "gold" }],
    layout: "row",
    cancelId: "cancel",
  }).then((r) => r.choice === "ok");
}

/** 多选项 / 带勾选框的完整弹窗（如关窗询问：托盘 / 退出 + 记住选择） */
export function showAppDialog(opts: DialogOptions): Promise<{ choice: string; checked: boolean }> {
  return open(opts);
}

const VARIANT_CLS: Record<NonNullable<DialogChoice["variant"]>, string> = {
  gold: "btn-gold flex-1",
  ghost: "btn-ghost flex-1",
  danger: "btn-halt flex-1",
};

export default function AppDialogHost() {
  const [cur, setCur] = useState<PendingDialog | null>(null);
  const [checked, setChecked] = useState(false);
  const [leaving, setLeaving] = useState(false);
  const curRef = useRef<PendingDialog | null>(null);
  curRef.current = cur;

  useEffect(() => {
    push = (d) => {
      setChecked(d.opts.checkbox?.defaultChecked ?? false);
      setLeaving(false);
      setCur(d);
    };
    return () => { push = null; };
  }, []);

  const finish = (choice: string) => {
    const d = curRef.current;
    if (!d) return;
    setLeaving(true);
    setTimeout(() => {
      setCur(null);
      setLeaving(false);
      d.resolve({ choice, checked });
    }, 140);
  };

  // Esc → cancelId；Enter → 主按钮（gold 优先，否则第一个）
  useEffect(() => {
    if (!cur) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") finish(cur.opts.cancelId ?? cur.opts.choices[cur.opts.choices.length - 1]?.id ?? "");
      else if (e.key === "Enter") {
        const gold = cur.opts.choices.find((c) => c.variant === "gold") ?? cur.opts.choices[0];
        if (gold) finish(gold.id);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [cur, checked]);

  if (!cur) return null;
  const { opts } = cur;
  const danger = opts.icon === "danger";

  return (
    <div className="fixed inset-0 z-[10000] flex items-center justify-center p-6"
      style={{ background: "rgba(4,6,9,0.72)", backdropFilter: "blur(4px)", WebkitBackdropFilter: "blur(4px)" }}
      onMouseDown={(e) => { if (e.target === e.currentTarget) finish(opts.cancelId ?? opts.choices[opts.choices.length - 1]?.id ?? ""); }}>
      <div className={`glass-bright w-full max-w-[400px] p-5 ${leaving ? "dialog-pop-out" : "dialog-pop"}`} style={{ borderRadius: 12 }}>
        {/* 标题行：图标 + 标题 + 关闭 X */}
        <div className="flex items-start gap-3">
          <span className={`w-9 h-9 shrink-0 rounded-lg flex items-center justify-center
            ${danger ? "bg-red/15 border border-red text-red" : "bg-gold/15 border border-gold/50 text-gold"}`}>
            {danger ? <span className="text-[18px] leading-none font-bold">!</span> : <span className="text-[16px] leading-none">i</span>}
          </span>
          <div className="flex-1 min-w-0 pt-1">
            <div className="font-mono text-[13px] font-bold text-ink leading-snug break-words">{opts.title}</div>
            {opts.detail && (
              <div className="font-mono text-[11px] text-ink-dim mt-1.5 leading-relaxed">{opts.detail}</div>
            )}
          </div>
          <button onClick={() => finish(opts.cancelId ?? opts.choices[opts.choices.length - 1]?.id ?? "")}
            className="text-ink-mute hover:text-ink transition-colors mt-0.5">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round">
              <path d="M18 6 6 18M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* 选项区 */}
        {opts.layout === "list" ? (
          <div className="mt-4 flex flex-col gap-2">
            {opts.choices.map((c) => (
              <button key={c.id} onClick={() => finish(c.id)}
                className={`w-full flex items-center gap-2.5 rounded-lg border px-3.5 py-2.5 font-mono text-[12.5px] font-semibold transition-all text-left
                  ${c.variant === "danger"
                    ? "border-red/40 bg-red/5 text-red hover:bg-red/15 hover:border-red"
                    : "border-line bg-elevated/60 text-ink hover:border-gold hover:bg-gold/5 hover:text-gold"}`}>
                <span className={c.variant === "danger" ? "text-red" : "text-gold"}>→</span>
                {c.label}
              </button>
            ))}
          </div>
        ) : (
          <div className="mt-5 flex items-center gap-2.5">
            {opts.choices.map((c) => (
              <button key={c.id} onClick={() => finish(c.id)} className={VARIANT_CLS[c.variant ?? "ghost"]}>
                {c.label}
              </button>
            ))}
          </div>
        )}

        {/* 记住我的选择 */}
        {opts.checkbox && (
          <label className="mt-4 flex items-center gap-2.5 cursor-pointer select-none group"
            onClick={(e) => { e.preventDefault(); setChecked((v) => !v); }}>
            <span className={`w-4 h-4 rounded border flex items-center justify-center transition-all shrink-0
              ${checked ? "bg-gold border-gold" : "border-active bg-elevated group-hover:border-gold"}`}>
              {checked && (
                <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="#0B0E11" strokeWidth="3.4" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M20 6 9 17l-5-5" />
                </svg>
              )}
            </span>
            <span className="font-mono text-[11px] text-ink-dim group-hover:text-ink transition-colors">{opts.checkbox.label}</span>
          </label>
        )}
      </div>
    </div>
  );
}
