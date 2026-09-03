import React, { useState, useEffect } from 'react';
import { History, Shield, RefreshCw } from 'lucide-react';
import { api } from '../api';

export function Audit() {
  const [logs, setLogs] = useState([]);
  const [loading, setLoading] = useState(true);

  const fetchAudit = async () => {
    try {
      const data = await api.getAuditLog(200);
      setLogs(data);
    } catch (err) {
      console.error('Failed to load audit log', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchAudit();
  }, []);

  return (
    <div className="space-y-6 animate-in fade-in duration-300">
      
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-white tracking-tight">Compliance & Access Audit Log</h1>
          <p className="text-xs text-slate-400 mt-0.5">
            Immutable record of all operator actions, access grants, content previews, and policy revisions
          </p>
        </div>

        <button
          onClick={fetchAudit}
          className="px-3 py-1.5 rounded-lg bg-surface-850 hover:bg-slate-700 text-slate-300 border border-slate-700 text-xs font-medium flex items-center space-x-1.5 transition-colors"
        >
          <RefreshCw className="w-3.5 h-3.5" />
          <span>Refresh Audit</span>
        </button>
      </div>

      {/* Audit Log Table */}
      <div className="glass-card rounded-xl border border-slate-800 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="bg-surface-850 text-slate-400 uppercase tracking-wider font-semibold border-b border-slate-800">
              <tr>
                <th className="px-4 py-3">Timestamp</th>
                <th className="px-4 py-3">Actor</th>
                <th className="px-4 py-3">Event Kind</th>
                <th className="px-4 py-3">Job ID</th>
                <th className="px-4 py-3">Source IP</th>
                <th className="px-4 py-3">Details / Justification</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60 font-mono">
              {logs.length > 0 ? (
                logs.map((log, idx) => (
                  <tr key={idx} className="hover:bg-slate-800/30 transition-colors">
                    <td className="px-4 py-3 text-slate-400 whitespace-nowrap">
                      {log.at ? new Date(log.at).toLocaleString() : '-'}
                    </td>
                    <td className="px-4 py-3 font-sans font-semibold text-slate-200">{log.actor}</td>
                    <td className="px-4 py-3">
                      <span className="px-2 py-0.5 rounded text-[10px] font-semibold bg-indigo-500/10 text-indigo-400 border border-indigo-500/30 uppercase">
                        {log.kind}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-slate-400 text-[11px] truncate max-w-xs" title={log.job_id}>
                      {log.job_id || '-'}
                    </td>
                    <td className="px-4 py-3 text-slate-400">{log.source_ip || '-'}</td>
                    <td className="px-4 py-3 font-sans text-slate-300 max-w-sm truncate" title={log.detail}>
                      {log.detail || '-'}
                    </td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan="6" className="px-4 py-8 text-center text-slate-500 font-sans">
                    {loading ? 'Loading audit records...' : 'No audit records logged yet.'}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

    </div>
  );
}
