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
              {halted ? `系统已挂起 (${t("panic.haltedTitle")})` : `紧急熔断 (${t("panic.haltTitle")})`}
            </div>
            <div className="font-mono text-[10px] text-ink-mute mt-0.5">
              CIRCUIT BREAKER // 全局执行通道
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
                确认后系统将 <span className="text-red font-bold">立即挂起全部执行通道</span>：
              </div>
              <ul className="mt-2.5 space-y-1.5 font-mono text-[11px] text-ink-dim">
                <li className="flex items-center gap-2"><I.X size={11} className="text-red" /> 撤销所有未成交挂单 (Open Orders)</li>
                <li className="flex items-center gap-2"><I.X size={11} className="text-red" /> 拒绝新的开仓/加仓指令</li>
                <li className="flex items-center gap-2"><I.X size={11} className="text-red" /> 现货 / 合约 / baw MPC 通道全部冻结</li>
                <li className="flex items-center gap-2"><I.X size={11} className="text-red" /> 多 Agent 议会停止派发执行指令</li>
              </ul>
            </>
          ) : (
            <>
              <div className="font-mono text-[12px] text-ink leading-relaxed">
                熔断已生效 — 所有执行通道已挂起。<span className="text-gold">仅在你确认风险解除后</span> 恢复操作。
              </div>
              <div className="mt-2.5 font-mono text-[10px] text-ink-mute">{t("panic.haltTime")}: {new Date().toLocaleTimeString()} // {t("panic.logRef")}: CB-{String(Date.now()).slice(-6)}</div>
            </>
          )}
        </div>

        {/* Actions */}
        <div className="mt-5 flex items-center gap-3 justify-end">
          {!halted ? (
            <>
              <button onClick={onClose} className="btn-ghost">取消</button>
              <button onClick={onConfirm} className="btn-halt"><I.Stop size={12} /> 确认熔断</button>
            </>
          ) : (
            <>
              <button onClick={onClose} className="btn-ghost">保持挂起</button>
              <button onClick={onResume} className="btn-gold"><I.Refresh size={12} /> RESUME · 恢复系统</button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
