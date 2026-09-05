import React from "react";

/* 从名字确定性生成的“软体脸”头像（Hermes blobatar 风格）。
 * avatar 字符串格式：
 *   "blobatar"           → 由名字 seed 生成（改名即换脸）
 *   "blobatar:<seed>"    → 锁定 seed（🎲 随机 / 🔒 固定）
 *   其它字符串（emoji）   → 直接渲染 emoji
 */

function fnv(str: string) {
  let h = 0x811c9dc5;
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}
// 用 hash 快速造伪随机序列
function rng(seed: number) {
  let s = seed || 1;
  return () => {
    s = Math.imul(s ^ (s >>> 15), 2246822507);
    s = Math.imul(s ^ (s >>> 13), 3266489909);
    s ^= s >>> 16;
    return (s >>> 0) / 4294967296;
  };
}

export function Blobatar({ seed, color, size = 40 }: { seed: string; color?: string; size?: number }) {
  const rand = rng(fnv("blob-" + seed));
  const bg = color || "#F0B90B";
  // 二次色（脸颊/高光）
  const tint = `hsl(${Math.round(rand() * 360)}, 60%, 70%)`;

  // 8 点扰动 → 软体 blob 路径（单位 100x100）
  const cx = 50, cy = 52, R = 40;
  const pts: number[] = [];
  for (let i = 0; i < 8; i++) {
    const a = (i / 8) * Math.PI * 2;
    const r = R * (0.78 + rand() * 0.34);
    pts.push(cx + Math.cos(a) * r, cy + Math.sin(a) * r);
  }
  // Catmull-Rom → 平滑贝塞尔
  const d: string[] = [];
  const n = pts.length / 2;
  for (let i = 0; i < n; i++) {
    const p0x = pts[(((i - 1 + n) % n) * 2)];
    const p0y = pts[(((i - 1 + n) % n) * 2) + 1];
    const p1x = pts[i * 2];
    const p1y = pts[i * 2 + 1];
    const p2x = pts[((i + 1) % n) * 2];
    const p2y = pts[((i + 1) % n) * 2 + 1];
    const p3x = pts[((i + 2) % n) * 2];
    const p3y = pts[((i + 2) % n) * 2 + 1];
    const c1x = p1x + (p2x - p0x) / 6, c1y = p1y + (p2y - p0y) / 6;
    const c2x = p2x - (p3x - p1x) / 6, c2y = p2y - (p3y - p1y) / 6;
    d.push(`${i === 0 ? "M" : "L"}${p1x.toFixed(1)} ${p1y.toFixed(1)} C${c1x.toFixed(1)} ${c1y.toFixed(1)}, ${c2x.toFixed(1)} ${c2y.toFixed(1)}, ${p2x.toFixed(1)} ${p2y.toFixed(1)}`);
  }
  d.push("Z");

  // 眼睛形态
  const eyeStyle = Math.floor(rand() * 4); // 0 open, 1 closed ^, 2 closed v, 3 round
  const eyeR = 3.2 + rand() * 1.4;
  const lookX = (rand() - 0.5) * 6;
  const lookY = (rand() - 0.5) * 4;
  const ex = cx - 17, ey = cy - 14;
  // 嘴
  const mouthStyle = Math.floor(rand() * 3); // 0 smile, 1 open happy, 2 flat
  const cheek = rand() > 0.45;

  const eye = (x: number) => {
    if (eyeStyle === 0) return <circle cx={x + lookX} cy={ey + lookY} r={eyeR} fill="#1a1d24" />;
    if (eyeStyle === 1) return <path d={`M${x - eyeR - 1} ${ey + eyeR - 1} Q${x} ${ey - eyeR - 3} ${x + eyeR + 1} ${ey + eyeR - 1}`} fill="none" stroke="#1a1d24" strokeWidth="2.4" strokeLinecap="round" />;
    if (eyeStyle === 2) return <path d={`M${x - eyeR - 1} ${ey - eyeR + 1} Q${x} ${ey + eyeR + 3} ${x + eyeR + 1} ${ey - eyeR + 1}`} fill="none" stroke="#1a1d24" strokeWidth="2.4" strokeLinecap="round" />;
    return <circle cx={x + lookX} cy={ey + lookY} r={eyeR + 1.6} fill="none" stroke="#1a1d24" strokeWidth="2.2" />;
  };

  const mouth = () => {
    const mx = cx, my = cy + 22;
    if (mouthStyle === 0) return <path d={`M${mx - 10} ${my - 3} Q${mx} ${my + 11} ${mx + 10} ${my - 3}`} fill="none" stroke="#1a1d24" strokeWidth="3" strokeLinecap="round" />;
    if (mouthStyle === 1) return <path d={`M${mx - 9} ${my} Q${mx} ${my + 13} ${mx + 9} ${my} Z`} fill="#1a1d24" />;
    return <path d={`M${mx - 10} ${my - 1} Q${mx} ${my + 4} ${mx + 10} ${my - 1}`} fill="none" stroke="#1a1d24" strokeWidth="2.6" strokeLinecap="round" />;
  };

  return (
    <svg width={size} height={size} viewBox="0 0 100 100" aria-label="avatar">
      <path d={d.join(" ")} fill={bg} />
      {cheek && <circle cx={cx - 26} cy={cy + 8} r={6.5} fill={tint} opacity={0.5} />}
      {cheek && <circle cx={cx + 26} cy={cy + 8} r={6.5} fill={tint} opacity={0.5} />}
      {eye(ex)}{eye(ex + 34)}
      {mouth()}
    </svg>
  );
}

/** 统一的 Agent 头像渲染：blobatar → SVG；其余 → emoji 圆。 */
export function AgentAvatar({ avatar, name, color, size = 34, ring = true }: {
  avatar?: string; name?: string; color?: string; size?: number; ring?: boolean;
}) {
  const av = avatar && avatar.trim() ? avatar : "🤖";
  const isBlob = av.startsWith("blobatar");
  if (isBlob) {
    // blobatar[:seed]
    const seed = av.split(":")[1] || name || "agent";
    const wrap = { width: size, height: size, borderRadius: "50%", overflow: "hidden" as const,
      border: ring ? `1px solid ${(color || "#F0B90B")}55` : "none", background: "#14161c" };
    return <div style={wrap}><Blobatar seed={seed} color={color} size={size} /></div>;
  }
  return (
    <span className="flex items-center justify-center shrink-0 select-none" style={{
      width: size, height: size, fontSize: Math.round(size * 0.58), borderRadius: "50%",
      background: `${(color || "#F0B90B")}22`, border: ring ? `1px solid ${(color || "#F0B90B")}66` : "none",
    }}>{av}</span>
  );
}
