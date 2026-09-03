import React, { useState, useEffect } from 'react';
import { Link, useSearchParams, useOutletContext } from 'react-router-dom';
import { Search, Filter, RefreshCw, ArrowUpDown } from 'lucide-react';
import { api } from '../api';
import { StateBadge } from '../components/StateBadge';
import { Modal } from '../components/Modal';
import { useAuth } from '../context/AuthContext';

const STATE_TABS = [
  { id: 'all', label: 'All' },
  { id: 'held', label: 'Held' },
  { id: 'released', label: 'Released' },
  { id: 'blocked', label: 'Blocked' },
  { id: 'released_then_flagged', label: 'Printed & Flagged' },
  { id: 'denied_by_analyst', label: 'Denied' },
  { id: 'failed_open', label: 'Failed Open' },
];

export function Queue() {
  const { autoSync } = useOutletContext();
  const { hasRole } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const currentState = searchParams.get('state') || 'all';

  const [jobs, setJobs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');

  // Decision Modal
  const [modalOpen, setModalOpen] = useState(false);
  const [selectedJob, setSelectedJob] = useState(null);
  const [decisionVerb, setDecisionVerb] = useState('release');
  const [reason, setReason] = useState('');
  const [actionLoading, setActionLoading] = useState(false);

  const fetchJobs = async () => {
    try {
      const params = {};
      if (currentState !== 'all') {
        params.state = currentState;
      }
      const data = await api.getJobs(params);
      setJobs(data);
    } catch (err) {
      console.error('Failed to fetch jobs', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchJobs();
    if (!autoSync) return;
    const interval = setInterval(fetchJobs, 1500);
    return () => clearInterval(interval);
  }, [currentState, autoSync]);

  const handleTabChange = (stateId) => {
    if (stateId === 'all') {
      searchParams.delete('state');
      setSearchParams(searchParams);
    } else {
      setSearchParams({ state: stateId });
    }
  };

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
      fetchJobs();
    } catch (err) {
      alert(`Action failed: ${err.message}`);
    } finally {
      setActionLoading(false);
    }
  };

  const filteredJobs = jobs.filter((job) => {
    if (!searchQuery) return true;
    const q = searchQuery.toLowerCase();
    return (
      job.title?.toLowerCase().includes(q) ||
      job.username?.toLowerCase().includes(q) ||
      job.queue?.toLowerCase().includes(q) ||
      job.hostname?.toLowerCase().includes(q)
    );
  });

  return (
    <div className="space-y-6 animate-in fade-in duration-300">
      
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight">Print Job Queue</h1>
          <p className="text-sm text-slate-400 mt-1">
            Inspected jobs, verdicts, and operator triage decisions
          </p>
        </div>

        {/* Search Bar */}
        <div className="relative w-full sm:w-80">
          <Search className="w-4 h-4 text-slate-400 absolute left-3.5 top-1/2 -translate-y-1/2 pointer-events-none" />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search document, user, queue..."
            className="w-full pl-10 pr-4 py-2 bg-surface-850 border border-slate-700 rounded-xl text-sm text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-indigo-500"
          />
        </div>
      </div>

      {/* State Filter Tabs */}
      <div className="flex flex-wrap items-center gap-2 p-1.5 rounded-2xl bg-surface-850 border border-slate-800">
        {STATE_TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => handleTabChange(tab.id)}
            className={`px-4 py-2 rounded-xl text-sm font-medium transition-all ${
              currentState === tab.id
                ? 'bg-indigo-600 text-white font-semibold shadow-md shadow-indigo-600/30'
                : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Jobs Table */}
      <div className="glass-card rounded-2xl border border-slate-800 overflow-hidden shadow-lg">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="bg-surface-850 text-slate-400 uppercase tracking-wider font-semibold border-b border-slate-800 text-xs">
              <tr>
                <th className="px-5 py-3.5">When</th>
                <th className="px-5 py-3.5">User</th>
                <th className="px-5 py-3.5">Host</th>
                <th className="px-5 py-3.5">Queue</th>
                <th className="px-5 py-3.5">Document</th>
                <th className="px-5 py-3.5">State</th>
                <th className="px-5 py-3.5">Rules</th>
                <th className="px-5 py-3.5">Score</th>
                <th className="px-5 py-3.5 text-right">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60">
              {filteredJobs.length > 0 ? (
                filteredJobs.map((job) => (
                  <tr key={job.id} className="hover:bg-slate-800/30 transition-colors">
                    <td className="px-5 py-4 font-mono text-slate-400 whitespace-nowrap text-xs">
                      {job.created_at ? new Date(job.created_at).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '-'}
                    </td>
                    <td className="px-5 py-4 font-semibold text-slate-200">{job.username}</td>
                    <td className="px-5 py-4 font-mono text-slate-400 text-xs">{job.hostname || '-'}</td>
                    <td className="px-5 py-4 text-slate-400">{job.queue}</td>
                    <td className="px-5 py-4 font-semibold text-white max-w-xs truncate">
                      <Link to={`/jobs/${job.id}`} className="hover:text-indigo-400 transition-colors">
                        {job.title || '(untitled)'}
                      </Link>
                    </td>
                    <td className="px-5 py-4">
                      <StateBadge state={job.state} />
                    </td>
                    <td className="px-5 py-4 font-mono text-slate-300 max-w-xs truncate text-xs">
                      {job.matches?.map((m) => m.rule_id).filter(Boolean).join(', ') || '-'}
                    </td>
                    <td className="px-5 py-4 font-mono font-bold text-slate-200">{job.score?.toFixed(2) || '0.00'}</td>
                    <td className="px-5 py-4 text-right whitespace-nowrap space-x-2">
                      {job.state === 'held' && hasRole('analyst') && (
                        <>
                          <button
                            onClick={() => handleDecisionClick(job, 'release')}
                            className="px-3 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white font-semibold text-xs transition-colors shadow-sm"
                          >
                            Release
                          </button>
                          <button
                            onClick={() => handleDecisionClick(job, 'deny')}
                            className="px-3 py-1.5 rounded-lg bg-rose-600/20 hover:bg-rose-600/30 text-rose-400 border border-rose-500/30 font-semibold text-xs transition-colors"
                          >
                            Deny
                          </button>
                        </>
                      )}
                    </td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan="9" className="px-5 py-10 text-center text-slate-500 text-sm">
                    {loading ? 'Loading jobs...' : 'No print jobs match the selected filter.'}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Decision Reason Modal */}
      <Modal
        isOpen={modalOpen}
        onClose={() => setModalOpen(false)}
        title={decisionVerb === 'release' ? 'Release Print Job' : 'Deny & Cancel Print Job'}
        maxWidth="max-w-xl"
      >
        <div className="space-y-4 text-sm">
          <p className="text-slate-300">
            {decisionVerb === 'release'
              ? 'This will immediately release the document to the physical printer. Provide a permanent audit reason.'
              : 'This will destroy the print spooler file permanently. Provide a reason for this denial.'}
          </p>
          
          <div>
            <label className="block font-medium text-slate-400 mb-1.5">Audit Justification</label>
            <input
              type="text"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="e.g., False positive, authorized HR export, verified clean"
              className="w-full px-3.5 py-2.5 bg-surface-850 border border-slate-700 rounded-xl text-sm text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              autoFocus
            />
          </div>

          <div className="flex justify-end space-x-3 pt-3">
            <button
              onClick={() => setModalOpen(false)}
              className="px-4 py-2.5 rounded-xl font-medium text-slate-400 hover:text-white hover:bg-slate-800 transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={submitDecision}
              disabled={actionLoading}
              className={`px-5 py-2.5 rounded-xl font-semibold text-white shadow-lg transition-all text-sm ${
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
