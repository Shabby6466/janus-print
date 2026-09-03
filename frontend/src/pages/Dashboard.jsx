import React, { useState, useEffect } from 'react';
import { Link, useOutletContext } from 'react-router-dom';
import { 
  ShieldAlert, 
  CheckCircle2, 
  XCircle, 
  AlertTriangle, 
  Activity, 
  Cpu, 
  Eye, 
  FileCheck, 
  ArrowUpRight,
  Clock
} from 'lucide-react';
import { api } from '../api';
import { StateBadge } from '../components/StateBadge';
import { Modal } from '../components/Modal';
import { useAuth } from '../context/AuthContext';

export function Dashboard() {
  const { autoSync } = useOutletContext();
  const { user, hasRole } = useAuth();
  const [stats, setStats] = useState(null);
  const [health, setHealth] = useState(null);
  const [loading, setLoading] = useState(true);

  // Decision Modal State
  const [modalOpen, setModalOpen] = useState(false);
  const [selectedJob, setSelectedJob] = useState(null);
  const [decisionVerb, setDecisionVerb] = useState('release');
  const [reason, setReason] = useState('');
  const [actionLoading, setActionLoading] = useState(false);

  const fetchData = async () => {
    try {
      const [statsData, healthData] = await Promise.all([
        api.getDashboardStats(),
        api.getHealth(),
      ]);
      setStats(statsData);
      setHealth(healthData);
    } catch (err) {
      console.error('Failed to fetch dashboard stats', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchData();
    if (!autoSync) return;
    const interval = setInterval(fetchData, 4000);
    return () => clearInterval(interval);
  }, [autoSync]);

  const handleDecisionClick = (job, verb) => {
    setSelectedJob(job);
    setDecisionVerb(verb);
    setReason('');
    setModalOpen(true);
  };

  const submitDecision = async () => {
    if (!reason || reason.trim().length < 3) {
      alert('Please provide a reason (minimum 3 characters)');
      return;
    }
    setActionLoading(true);
    try {
      if (decisionVerb === 'release') {
        await api.releaseJob(selectedJob.id, reason.trim());
      } else {
        await api.denyJob(selectedJob.id, reason.trim());
      }
      setModalOpen(false);
      fetchData();
    } catch (err) {
      alert(`Action failed: ${err.message}`);
    } finally {
      setActionLoading(false);
    }
  };

  if (loading && !stats) {
    return (
      <div className="flex items-center justify-center py-24 text-slate-400 font-mono text-sm">
        <Activity className="w-5 h-5 animate-spin mr-2 text-indigo-400" />
        Loading inspection metrics...
      </div>
    );
  }

  const counts = stats?.counts || {};

  return (
    <div className="space-y-8 animate-in fade-in duration-300">
      
      {/* Top Banner / System Health */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 p-4 rounded-xl bg-surface-850 border border-slate-800">
        <div>
          <h1 className="text-xl font-bold text-white tracking-tight">Print Inspection Dashboard</h1>
          <p className="text-xs text-slate-400 mt-0.5">
            {stats?.rules_loaded || 0} active DLP rules &middot; Spooler mode: <span className="font-mono text-slate-300">{stats?.cups_mode || 'cupsd'}</span>
          </p>
        </div>

        {health && (
          <div className="flex flex-wrap items-center gap-2 text-xs font-mono">
            <span className="flex items-center space-x-1 px-2.5 py-1 rounded bg-slate-800 text-slate-300 border border-slate-700">
              <Cpu className="w-3.5 h-3.5 text-indigo-400" />
              <span>RE2 Linear: {health.regex_linear_time ? 'ON' : 'OFF'}</span>
            </span>
            <span className="flex items-center space-x-1 px-2.5 py-1 rounded bg-slate-800 text-slate-300 border border-slate-700">
              <Eye className="w-3.5 h-3.5 text-indigo-400" />
              <span>OCR: {health.ocr_available ? 'READY' : 'UNAVAILABLE'}</span>
            </span>
            <span className={`px-2.5 py-1 rounded border font-semibold ${
              health.status === 'ok' ? 'bg-emerald-950/40 text-emerald-400 border-emerald-800' : 'bg-rose-950/40 text-rose-400 border-rose-800'
            }`}>
              {health.status.toUpperCase()}
            </span>
          </div>
        )}
      </div>

      {/* Coverage Gap / Approval Banners */}
      {stats?.gaps > 0 && (
        <div className="p-4 rounded-xl bg-amber-500/10 border border-amber-500/30 flex items-start space-x-3 text-xs text-amber-300">
          <AlertTriangle className="w-5 h-5 text-amber-400 shrink-0 mt-0.5" />
          <div>
            <span className="font-bold">{stats.gaps} print job(s) released without inspection.</span>
            <p className="mt-0.5 text-amber-300/80">
              The inspector was unavailable and those queues are fail-open. These are coverage gaps — <Link to="/queue?state=failed_open" className="underline font-semibold hover:text-white">review them in Queue</Link>.
            </p>
          </div>
        </div>
      )}

      {stats?.pending_requests > 0 && (
        <div className="p-4 rounded-xl bg-indigo-500/10 border border-indigo-500/30 flex items-start space-x-3 text-xs text-indigo-300">
          <FileCheck className="w-5 h-5 text-indigo-400 shrink-0 mt-0.5" />
          <div>
            <span className="font-bold">{stats.pending_requests} pending document access request(s) awaiting dual approval.</span>
            <p className="mt-0.5 text-indigo-300/80">
              A second approver is required to unlock full document archive access — <Link to="/audit" className="underline font-semibold hover:text-white">review in Audit</Link>.
            </p>
          </div>
        </div>
      )}

      {/* Metric Cards Grid */}
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-4">
        {[
          { label: 'Held', count: counts.held || 0, color: 'text-amber-400', border: 'border-amber-500/20' },
          { label: 'Released', count: (counts.released || 0) + (counts.released_by_analyst || 0), color: 'text-emerald-400', border: 'border-emerald-500/20' },
          { label: 'Blocked', count: counts.blocked || 0, color: 'text-rose-400', border: 'border-rose-500/20' },
          { label: 'Flagged After', count: counts.released_then_flagged || 0, color: 'text-purple-400', border: 'border-purple-500/20' },
          { label: 'Denied', count: counts.denied_by_analyst || 0, color: 'text-rose-300', border: 'border-rose-500/20' },
          { label: 'Coverage Gaps', count: stats?.gaps || 0, color: 'text-orange-400', border: 'border-orange-500/20' },
        ].map((c) => (
          <div key={c.label} className={`glass-card rounded-xl p-4 border ${c.border} flex flex-col justify-between`}>
            <div className={`text-2xl font-bold font-mono ${c.color}`}>{c.count}</div>
            <div className="text-xs font-medium text-slate-400 mt-1">{c.label}</div>
          </div>
        ))}
      </div>

      {/* Awaiting Review Queue */}
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold text-white flex items-center space-x-2">
            <ShieldAlert className="w-4 h-4 text-amber-400" />
            <span>Awaiting SOC Review</span>
          </h2>
          {stats?.held?.length > 0 && (
            <Link to="/queue?state=held" className="text-xs text-indigo-400 hover:text-indigo-300 flex items-center space-x-1">
              <span>View all held ({stats.held.length})</span>
              <ArrowUpRight className="w-3.5 h-3.5" />
            </Link>
          )}
        </div>

        {stats?.held?.length > 0 ? (
          <div className="glass-card rounded-xl border border-slate-800 overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead className="bg-surface-850 text-slate-400 uppercase tracking-wider font-semibold border-b border-slate-800">
                  <tr>
                    <th className="px-4 py-3">When</th>
                    <th className="px-4 py-3">User</th>
                    <th className="px-4 py-3">Queue</th>
                    <th className="px-4 py-3">Document</th>
                    <th className="px-4 py-3">Verdict Reason</th>
                    <th className="px-4 py-3">Score</th>
                    <th className="px-4 py-3 text-right">Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/60">
                  {stats.held.map((job) => (
                    <tr key={job.id} className="hover:bg-slate-800/30 transition-colors">
                      <td className="px-4 py-3 font-mono text-slate-400 whitespace-nowrap">
                        {job.created_at ? new Date(job.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '-'}
                      </td>
                      <td className="px-4 py-3 font-medium text-slate-200">{job.username}</td>
                      <td className="px-4 py-3 text-slate-400">{job.queue}</td>
                      <td className="px-4 py-3 font-medium text-white max-w-xs truncate">
                        <Link to={`/jobs/${job.id}`} className="hover:text-indigo-400 transition-colors">
                          {job.title || '(untitled)'}
                        </Link>
                      </td>
                      <td className="px-4 py-3 text-slate-300 max-w-sm truncate">{job.verdict_reason || '-'}</td>
                      <td className="px-4 py-3 font-mono text-slate-200">{job.score?.toFixed(2) || '0.00'}</td>
                      <td className="px-4 py-3 text-right whitespace-nowrap space-x-2">
                        {hasRole('analyst') && (
                          <>
                            <button
                              onClick={() => handleDecisionClick(job, 'release')}
                              className="px-2.5 py-1 rounded bg-indigo-600 hover:bg-indigo-500 text-white font-medium transition-colors"
                            >
                              Release
                            </button>
                            <button
                              onClick={() => handleDecisionClick(job, 'deny')}
                              className="px-2.5 py-1 rounded bg-rose-600/20 hover:bg-rose-600/30 text-rose-400 border border-rose-500/30 font-medium transition-colors"
                            >
                              Deny
                            </button>
                          </>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ) : (
          <div className="glass-card rounded-xl p-8 border border-slate-800 text-center text-xs text-slate-500">
            <CheckCircle2 className="w-8 h-8 text-emerald-500/40 mx-auto mb-2" />
            Nothing held. Every print job cleared inspection.
          </div>
        )}
      </div>

      {/* Recent Jobs Live Stream */}
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold text-white flex items-center space-x-2">
            <Clock className="w-4 h-4 text-indigo-400" />
            <span>Recent Print Stream</span>
          </h2>
          <Link to="/queue" className="text-xs text-indigo-400 hover:text-indigo-300 flex items-center space-x-1">
            <span>Full Queue</span>
            <ArrowUpRight className="w-3.5 h-3.5" />
          </Link>
        </div>

        <div className="glass-card rounded-xl border border-slate-800 overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <thead className="bg-surface-850 text-slate-400 uppercase tracking-wider font-semibold border-b border-slate-800">
                <tr>
                  <th className="px-4 py-3">When</th>
                  <th className="px-4 py-3">User</th>
                  <th className="px-4 py-3">Queue</th>
                  <th className="px-4 py-3">Document</th>
                  <th className="px-4 py-3">State</th>
                  <th className="px-4 py-3">Tier</th>
                  <th className="px-4 py-3">Pages</th>
                  <th className="px-4 py-3">Latency</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-800/60">
                {stats?.recent?.map((job) => (
                  <tr key={job.id} className="hover:bg-slate-800/30 transition-colors">
                    <td className="px-4 py-3 font-mono text-slate-400 whitespace-nowrap">
                      {job.created_at ? new Date(job.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : '-'}
                    </td>
                    <td className="px-4 py-3 font-medium text-slate-200">{job.username}</td>
                    <td className="px-4 py-3 text-slate-400">{job.queue}</td>
                    <td className="px-4 py-3 font-medium text-white max-w-xs truncate">
                      <Link to={`/jobs/${job.id}`} className="hover:text-indigo-400 transition-colors">
                        {job.title || '(untitled)'}
                      </Link>
                    </td>
                    <td className="px-4 py-3">
                      <StateBadge state={job.state} />
                    </td>
                    <td className="px-4 py-3 font-mono text-slate-400">{job.scan_tier || 'text'}</td>
                    <td className="px-4 py-3 font-mono text-slate-300">{job.page_count}</td>
                    <td className="px-4 py-3 font-mono text-slate-400">{job.inline_ms}ms</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>

      {/* Decision Reason Modal */}
      <Modal
        isOpen={modalOpen}
        onClose={() => setModalOpen(false)}
        title={decisionVerb === 'release' ? 'Release Print Job' : 'Deny & Cancel Print Job'}
      >
        <div className="space-y-4">
          <p className="text-xs text-slate-300">
            {decisionVerb === 'release'
              ? 'This will immediately release the document to the physical printer. Provide a permanent audit reason.'
              : 'This will destroy the print spooler file permanently. Provide a reason for this denial.'}
          </p>
          
          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">Audit Justification</label>
            <input
              type="text"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="e.g., False positive, authorized HR export, verified clean"
              className="w-full px-3 py-2 bg-surface-850 border border-slate-700 rounded-lg text-sm text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              autoFocus
            />
          </div>

          <div className="flex justify-end space-x-2 pt-2">
            <button
              onClick={() => setModalOpen(false)}
              className="px-4 py-2 rounded-lg text-xs font-medium text-slate-400 hover:text-white hover:bg-slate-800 transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={submitDecision}
              disabled={actionLoading}
              className={`px-4 py-2 rounded-lg text-xs font-semibold text-white shadow-lg transition-all ${
                decisionVerb === 'release'
                  ? 'bg-indigo-600 hover:bg-indigo-500 shadow-indigo-600/30'
                  : 'bg-rose-600 hover:bg-rose-500 shadow-rose-600/30'
              }`}
            >
              {actionLoading ? 'Applying...' : decisionVerb === 'release' ? 'Confirm Release' : 'Confirm Deny'}
            </button>
          </div>
        </div>
      </Modal>

    </div>
  );
}
