import React, { useState, useEffect } from 'react';
import { useParams, Link, useNavigate } from 'react-router-dom';
import { 
  ArrowLeft, 
  Eye, 
  ShieldCheck, 
  ShieldX, 
  Clock, 
  FileText, 
  Cpu, 
  AlertTriangle, 
  Lock, 
  CheckCircle2, 
  UserCheck 
} from 'lucide-react';
import { api } from '../api';
import { StateBadge } from '../components/StateBadge';
import { Modal } from '../components/Modal';
import { useAuth } from '../context/AuthContext';

export function JobDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { user, hasRole } = useAuth();

  const [job, setJob] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  // Decision Modal
  const [decisionModal, setDecisionModal] = useState(false);
  const [decisionVerb, setDecisionVerb] = useState('release');
  const [reason, setReason] = useState('');
  const [actionLoading, setActionLoading] = useState(false);

  // Content Request Modal
  const [requestModal, setRequestModal] = useState(false);
  const [requestReason, setRequestReason] = useState('');

  const fetchJob = async () => {
    try {
      const data = await api.getJob(id);
      setJob(data);
    } catch (err) {
      setError(err.message || 'Failed to load job');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchJob();
    // Auto-poll if job is pending OCR
    const interval = setInterval(() => {
      if (job?.scan_tier === 'ocr_pending' || job?.state === 'held') {
        fetchJob();
      }
    }, 3000);
    return () => clearInterval(interval);
  }, [id, job?.scan_tier, job?.state]);

  const handleDecision = async () => {
    if (!reason || reason.trim().length < 3) {
      alert('Please provide an audit reason');
      return;
    }
    setActionLoading(true);
    try {
      if (decisionVerb === 'release') {
        await api.releaseJob(id, reason.trim());
      } else {
        await api.denyJob(id, reason.trim());
      }
      setDecisionModal(false);
      fetchJob();
    } catch (err) {
      alert(`Decision failed: ${err.message}`);
    } finally {
      setActionLoading(false);
    }
  };

  const handleContentRequest = async () => {
    if (!requestReason || requestReason.trim().length < 3) {
      alert('Please state why you need to read this document');
      return;
    }
    setActionLoading(true);
    try {
      await api.requestContent(id, requestReason.trim());
      setRequestModal(false);
      fetchJob();
    } catch (err) {
      alert(`Request failed: ${err.message}`);
    } finally {
      setActionLoading(false);
    }
  };

  const handleApproveContent = async (requestId) => {
    if (!confirm('Approve this content access request? This action is logged permanently.')) return;
    try {
      await api.approveContent(requestId);
      fetchJob();
    } catch (err) {
      alert(`Approval failed: ${err.message}`);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24 text-slate-400 font-mono text-base">
        Loading job #{id}...
      </div>
    );
  }

  if (error || !job) {
    return (
      <div className="p-6 rounded-2xl bg-rose-500/10 border border-rose-500/30 text-rose-400 text-sm">
        {error || 'Job not found'}
      </div>
    );
  }

  return (
    <div className="space-y-8 animate-in fade-in duration-300">
      
      {/* Back Button & Header */}
      <div>
        <button
          onClick={() => navigate('/queue')}
          className="inline-flex items-center space-x-2 text-sm text-slate-400 hover:text-white transition-colors mb-3"
        >
          <ArrowLeft className="w-4 h-4" />
          <span>Back to Queue</span>
        </button>

        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
          <div>
            <div className="flex items-center space-x-3.5">
              <h1 className="text-2xl font-bold text-white tracking-tight">{job.title || '(untitled)'}</h1>
              <StateBadge state={job.state} />
            </div>
            <p className="text-sm text-slate-400 mt-1">
              Printed by <span className="text-slate-200 font-semibold">{job.username}</span> on{' '}
              <span className="text-slate-200 font-semibold">{job.queue}</span> &middot;{' '}
              {job.created_at ? new Date(job.created_at).toLocaleString() : '-'}
            </p>
          </div>

          {/* Action Buttons */}
          <div className="flex items-center space-x-3">
            <Link
              to={`/jobs/${job.id}/view`}
              className="px-4 py-2 rounded-xl bg-surface-850 hover:bg-slate-700 text-slate-200 border border-slate-700 text-sm font-semibold flex items-center space-x-2 transition-colors shadow-sm"
            >
              <Eye className="w-4 h-4 text-indigo-400" />
              <span>View Pages</span>
            </Link>

            {job.state === 'held' && hasRole('analyst') && (
              <>
                <button
                  onClick={() => { setDecisionVerb('release'); setReason(''); setDecisionModal(true); }}
                  className="px-4 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-sm font-semibold shadow-lg shadow-indigo-600/30 flex items-center space-x-2 transition-all"
                >
                  <ShieldCheck className="w-4 h-4" />
                  <span>Release to Printer</span>
                </button>
                <button
                  onClick={() => { setDecisionVerb('deny'); setReason(''); setDecisionModal(true); }}
                  className="px-4 py-2 rounded-xl bg-rose-600/20 hover:bg-rose-600/30 text-rose-400 border border-rose-500/30 text-sm font-semibold flex items-center space-x-2 transition-colors"
                >
                  <ShieldX className="w-4 h-4" />
                  <span>Deny & Cancel</span>
                </button>
              </>
            )}
          </div>
        </div>
      </div>

      {/* Main Grid: Verdict & Job Info */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        
        {/* Verdict Card */}
        <div className="glass-card rounded-2xl p-6 border border-slate-800 space-y-4 shadow-md">
          <h3 className="text-base font-semibold text-white flex items-center space-x-2.5 border-b border-slate-800/80 pb-3.5">
            <Cpu className="w-5 h-5 text-indigo-400" />
            <span>Inspection Verdict</span>
          </h3>

          <dl className="grid grid-cols-2 gap-x-6 gap-y-4 text-sm">
            <div>
              <dt className="text-slate-400 font-medium">Action</dt>
              <dd className="text-white font-mono uppercase font-bold text-base mt-1">{job.action}</dd>
            </div>
            <div>
              <dt className="text-slate-400 font-medium">Confidence Score</dt>
              <dd className="text-white font-mono font-bold text-base mt-1">{job.score?.toFixed(2) || '0.00'}</dd>
            </div>
            <div className="col-span-2">
              <dt className="text-slate-400 font-medium">Verdict Reason</dt>
              <dd className="text-slate-200 mt-1 font-medium">{job.verdict_reason || '-'}</dd>
            </div>
            <div>
              <dt className="text-slate-400 font-medium">Scan Tier</dt>
              <dd className="text-indigo-300 font-mono mt-1 font-semibold">{job.scan_tier}</dd>
            </div>
            <div>
              <dt className="text-slate-400 font-medium">Pages</dt>
              <dd className="text-slate-200 font-mono mt-1">
                {job.page_count} ({job.pages_without_text} graphical/thin)
              </dd>
            </div>
            <div>
              <dt className="text-slate-400 font-medium">Inline Latency</dt>
              <dd className="text-slate-200 font-mono mt-1">{job.inline_ms} ms</dd>
            </div>
          </dl>
        </div>

        {/* Job Metadata Card */}
        <div className="glass-card rounded-2xl p-6 border border-slate-800 space-y-4 shadow-md">
          <h3 className="text-base font-semibold text-white flex items-center space-x-2.5 border-b border-slate-800/80 pb-3.5">
            <FileText className="w-5 h-5 text-indigo-400" />
            <span>Job Metadata</span>
          </h3>

          <dl className="grid grid-cols-2 gap-x-6 gap-y-4 text-sm">
            <div>
              <dt className="text-slate-400 font-medium">Job ID</dt>
              <dd className="text-slate-300 font-mono text-xs truncate mt-1" title={job.id}>{job.id}</dd>
            </div>
            <div>
              <dt className="text-slate-400 font-medium">CUPS Spool ID</dt>
              <dd className="text-slate-200 font-mono mt-1 font-semibold">{job.queue}-{job.cups_job_id}</dd>
            </div>
            <div>
              <dt className="text-slate-400 font-medium">Workstation Host</dt>
              <dd className="text-slate-200 font-mono mt-1">{job.hostname || 'unknown'}</dd>
            </div>
            <div>
              <dt className="text-slate-400 font-medium">Copies</dt>
              <dd className="text-slate-200 font-mono mt-1 font-semibold">{job.copies}</dd>
            </div>
            <div className="col-span-2">
              <dt className="text-slate-400 font-medium">SHA-256 Hash</dt>
              <dd className="text-slate-400 font-mono text-xs break-all mt-1">{job.sha256}</dd>
            </div>
          </dl>
        </div>

      </div>

      {/* Matched DLP Rules */}
      <div className="space-y-4">
        <h3 className="text-base font-bold text-white flex items-center space-x-2">
          <span>DLP Rule Matches ({job.matches?.length || 0})</span>
        </h3>

        {job.matches?.length > 0 ? (
          <div className="glass-card rounded-2xl border border-slate-800 overflow-hidden shadow-lg">
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead className="bg-surface-850 text-slate-400 uppercase tracking-wider font-semibold border-b border-slate-800 text-xs">
                  <tr>
                    <th className="px-5 py-3.5">Rule Name & ID</th>
                    <th className="px-5 py-3.5">Severity</th>
                    <th className="px-5 py-3.5">Count</th>
                    <th className="px-5 py-3.5">Score</th>
                    <th className="px-5 py-3.5">Tier</th>
                    <th className="px-5 py-3.5">Page</th>
                    <th className="px-5 py-3.5">Masked Sample</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/60 font-mono">
                  {job.matches.map((m, idx) => (
                    <tr key={idx} className="hover:bg-slate-800/30 transition-colors">
                      <td className="px-5 py-4 font-sans">
                        <div className="font-bold text-white text-base">{m.rule_name || m.rule_id}</div>
                        <div className="text-xs text-slate-400 font-mono mt-0.5">{m.rule_id}</div>
                      </td>
                      <td className="px-5 py-4 text-slate-300 font-bold">{m.severity}</td>
                      <td className="px-5 py-4 text-slate-300">{m.count}</td>
                      <td className="px-5 py-4 text-slate-200 font-bold">{m.score?.toFixed(2)}</td>
                      <td className="px-5 py-4 text-indigo-400 font-semibold">{m.tier}</td>
                      <td className="px-5 py-4 text-slate-300">{m.page}</td>
                      <td className="px-5 py-4 text-slate-300 text-sm tracking-widest font-bold">
                        {m.sample || '-'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ) : (
          <div className="glass-card rounded-2xl p-6 border border-slate-800 text-sm text-slate-500">
            No rule matches recorded for this print job.
          </div>
        )}
      </div>

      {/* Archived Document & Dual Approval Panel */}
      <div className="glass-card rounded-2xl p-6 border border-slate-800 space-y-5 shadow-md">
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 border-b border-slate-800/80 pb-4">
          <div>
            <h3 className="text-base font-bold text-white flex items-center space-x-2.5">
              <Lock className="w-5 h-5 text-amber-400" />
              <span>Encrypted Document Archive & Dual-Approval Gate</span>
            </h3>
            <p className="text-sm text-slate-400 mt-1">
              Reading or downloading unmasked document contents requires justification and a second approver.
            </p>
          </div>

          {hasRole('analyst') && (
            <button
              onClick={() => { setRequestReason(''); setRequestModal(true); }}
              className="px-4 py-2 rounded-xl bg-surface-850 hover:bg-slate-700 text-slate-200 border border-slate-700 text-sm font-semibold transition-colors self-start sm:self-auto shadow-sm"
            >
              Request Access
            </button>
          )}
        </div>

        {/* Pending / Active Content Requests */}
        {job.content_requests?.length > 0 && (
          <div className="space-y-3">
            <h4 className="text-sm font-semibold text-slate-300">Access Requests</h4>
            <div className="divide-y divide-slate-800 border border-slate-800 rounded-xl overflow-hidden">
              {job.content_requests.map((cr) => (
                <div key={cr.id} className="p-4 bg-surface-850 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 text-sm">
                  <div>
                    <span className="font-bold text-white">{cr.requested_by}</span>: {cr.reason}
                    <div className="text-xs text-slate-400 mt-1">
                      Status: <span className="font-bold text-amber-400 uppercase">{cr.state}</span> &middot; Requested {new Date(cr.requested_at).toLocaleString()}
                    </div>
                  </div>

                  {cr.state === 'pending' && hasRole('approver') && cr.requested_by !== user.username && (
                    <button
                      onClick={() => handleApproveContent(cr.id)}
                      className="px-3.5 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white font-semibold text-xs flex items-center space-x-1.5 self-start sm:self-auto"
                    >
                      <UserCheck className="w-4 h-4" />
                      <span>Approve Request</span>
                    </button>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* History Timeline */}
      <div className="glass-card rounded-2xl p-6 border border-slate-800 space-y-4 shadow-md">
        <h3 className="text-base font-bold text-white flex items-center space-x-2.5 border-b border-slate-800/80 pb-3.5">
          <Clock className="w-5 h-5 text-indigo-400" />
          <span>Audit Event Timeline</span>
        </h3>

        <div className="space-y-3 font-mono text-sm">
          {job.events?.map((ev, idx) => (
            <div key={idx} className="flex items-start space-x-3 text-slate-300">
              <span className="text-slate-400 text-xs whitespace-nowrap">
                {ev.at ? new Date(ev.at).toLocaleTimeString() : '-'}
              </span>
              <span className="font-bold text-indigo-300">{ev.event}</span>
              <span className="text-slate-400 font-sans">by <strong className="text-slate-200">{ev.actor}</strong>: {ev.detail || '-'}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Decision Modal */}
      <Modal
        isOpen={decisionModal}
        onClose={() => setDecisionModal(false)}
        title={decisionVerb === 'release' ? 'Release Print Job' : 'Deny & Cancel Print Job'}
        maxWidth="max-w-xl"
      >
        <div className="space-y-4 text-sm">
          <p className="text-slate-300">
            {decisionVerb === 'release'
              ? 'This will immediately release the document to the physical printer. Provide an audit justification.'
              : 'This will destroy the print spooler file permanently. Provide a reason for this denial.'}
          </p>
          
          <div>
            <label className="block font-medium text-slate-400 mb-1.5">Reason</label>
            <input
              type="text"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="e.g., Verified false positive, authorized executive export"
              className="w-full px-3.5 py-2.5 bg-surface-850 border border-slate-700 rounded-xl text-sm text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              autoFocus
            />
          </div>

          <div className="flex justify-end space-x-3 pt-3">
            <button
              onClick={() => setDecisionModal(false)}
              className="px-4 py-2.5 rounded-xl font-medium text-slate-400 hover:text-white hover:bg-slate-800 transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleDecision}
              disabled={actionLoading}
              className={`px-5 py-2.5 rounded-xl font-semibold text-white shadow-lg transition-all text-sm ${
                decisionVerb === 'release'
                  ? 'bg-indigo-600 hover:bg-indigo-500 shadow-indigo-600/30'
                  : 'bg-rose-600 hover:bg-rose-500 shadow-rose-600/30'
              }`}
            >
              {actionLoading ? 'Applying...' : decisionVerb === 'release' ? 'Release to Printer' : 'Deny and Cancel'}
            </button>
          </div>
        </div>
      </Modal>

      {/* Content Request Modal */}
      <Modal
        isOpen={requestModal}
        onClose={() => setRequestModal(false)}
        title="Request Document Archive Access"
        maxWidth="max-w-xl"
      >
        <div className="space-y-4 text-sm">
          <p className="text-slate-300">
            To prevent unauthorized viewing of sensitive files, document content access requires justification and approval by a second authorized user.
          </p>
          
          <div>
            <label className="block font-medium text-slate-400 mb-1.5">Business Justification</label>
            <textarea
              rows={3}
              value={requestReason}
              onChange={(e) => setRequestReason(e.target.value)}
              placeholder="e.g., Incident investigation INC-8821, investigating suspected data exfiltration"
              className="w-full px-3.5 py-2.5 bg-surface-850 border border-slate-700 rounded-xl text-sm text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              autoFocus
            />
          </div>

          <div className="flex justify-end space-x-3 pt-3">
            <button
              onClick={() => setRequestModal(false)}
              className="px-4 py-2.5 rounded-xl font-medium text-slate-400 hover:text-white hover:bg-slate-800 transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleContentRequest}
              disabled={actionLoading}
              className="px-5 py-2.5 rounded-xl font-semibold text-white bg-indigo-600 hover:bg-indigo-500 shadow-lg shadow-indigo-600/30 transition-all text-sm"
            >
              {actionLoading ? 'Submitting...' : 'Submit Access Request'}
            </button>
          </div>
        </div>
      </Modal>

    </div>
  );
}
