import React from "react";
import { I } from "../components/icons";
import { useT } from "../i18n/i18n";

export function PanicHaltModal({ isOpen, onClose, halted, onConfirm, onResume }: {
  isOpen: boolean;
  onClose: () => void;
  halted: boolean;
  onConfirm: () => void;
  onResume: () => void;
}) {
  const t = useT();
  if (!isOpen) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-6" style={{ background: "rgba(4,6,9,0.72)", backdropFilter: "blur(4px)" }}>
      <div className="glass-bright w-full max-w-md p-5" style={{ borderRadius: 12 }}>
        {/* Header */}
        <div className="flex items-center gap-3">
          <span className="w-9 h-9 rounded-lg bg-red/15 border border-red flex items-center justify-center text-red">
            <I.Stop size={16} />
          </span>
          <div>
            <div className={`font-mono text-[13px] font-bold tracking-wider ${halted ? "text-red" : "text-ink"}`}>
              {t("panic.haltedTitle")}
            </div>
            <div className="font-mono text-[10px] text-ink-mute mt-0.5">
              {t("panic.subtitle")}
            </div>
          </div>
          <button onClick={onClose} className="ml-auto text-ink-mute hover:text-ink transition-colors">
            <I.X size={16} />
          </button>
        </div>

        {/* Body */}
        <div className="mt-4 rounded-lg border border-red/30 bg-red/5 p-3.5">
          {!halted ? (
            <>
              <div className="font-mono text-[12px] text-ink leading-relaxed">
                {t("panic.confirmLead")} <span className="text-red font-bold">{t("panic.confirmEm")}</span>：
              </div>
              <ul className="mt-2.5 space-y-1.5 font-mono text-[11px] text-ink-dim">
                <li className="flex items-center gap-2"><I.X size={11} className="text-red" /> {t("panic.listCancel")}</li>
                <li className="flex items-center gap-2"><I.X size={11} className="text-red" /> {t("panic.listReject")}</li>
                <li className="flex items-center gap-2"><I.X size={11} className="text-red" /> {t("panic.listFreeze")}</li>
                <li className="flex items-center gap-2"><I.X size={11} className="text-red" /> {t("panic.listStop")}</li>
              </ul>
            </>
          ) : (
            <>
              <div className="font-mono text-[12px] text-ink leading-relaxed">
                {t("panic.active1")} <span className="text-gold">{t("panic.active2")}</span> {t("panic.active3")}
              </div>
              <div className="mt-2.5 font-mono text-[10px] text-ink-mute">{t("panic.haltTime")}: {new Date().toLocaleTimeString()} // {t("panic.logRef")}: CB-{String(Date.now()).slice(-6)}</div>
            </>
          )}
        </div>

        {/* Actions */}
        <div className="mt-5 flex items-center gap-3 justify-end">
          {!halted ? (
            <>
              <button onClick={onClose} className="btn-ghost">{t("panic.btnCancel")}</button>
              <button onClick={onConfirm} className="btn-halt"><I.Stop size={12} /> {t("panic.btnHalt")}</button>
            </>
          ) : (
            <>
              <button onClick={onClose} className="btn-ghost">{t("panic.btnHeld")}</button>
              <button onClick={onResume} className="btn-gold"><I.Refresh size={12} /> {t("panic.btnResume")}</button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
