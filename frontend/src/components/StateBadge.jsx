import React from 'react';

const STATE_CONFIG = {
  held: { label: 'Held', bg: 'bg-amber-500/10 text-amber-400 border-amber-500/20' },
  released: { label: 'Released', bg: 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20' },
  released_by_analyst: { label: 'Released by Analyst', bg: 'bg-teal-500/10 text-teal-400 border-teal-500/20' },
  released_then_flagged: { label: 'Printed, Flagged After', bg: 'bg-purple-500/10 text-purple-400 border-purple-500/20' },
  denied_by_analyst: { label: 'Denied', bg: 'bg-rose-500/10 text-rose-400 border-rose-500/20' },
  blocked: { label: 'Blocked', bg: 'bg-red-500/10 text-red-400 border-red-500/20' },
  failed_open: { label: 'Failed Open', bg: 'bg-orange-500/10 text-orange-400 border-orange-500/20' },
  inspecting: { label: 'Inspecting', bg: 'bg-blue-500/10 text-blue-400 border-blue-500/20' },
  ocr_pending: { label: 'OCR Pending', bg: 'bg-amber-500/10 text-amber-400 border-amber-500/20' },
  ocr_complete: { label: 'OCR Complete', bg: 'bg-indigo-500/10 text-indigo-400 border-indigo-500/20' },
};

export function StateBadge({ state, className = '' }) {
  const config = STATE_CONFIG[state] || { label: state || 'Unknown', bg: 'bg-slate-500/10 text-slate-400 border-slate-500/20' };

  return (
    <span
      className={`inline-flex items-center px-3 py-1 rounded-full text-xs font-semibold border ${config.bg} ${className}`}
    >
      {config.label}
    </span>
  );
}
