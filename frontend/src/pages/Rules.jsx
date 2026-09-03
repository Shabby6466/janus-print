import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { 
  Plus, 
  Search, 
  SlidersHorizontal, 
  Trash2, 
  Edit3, 
  CheckCircle, 
  XCircle, 
  ShieldAlert,
  AlertCircle 
} from 'lucide-react';
import { api } from '../api';
import { Modal } from '../components/Modal';
import { useAuth } from '../context/AuthContext';

export function Rules() {
  const { hasRole } = useAuth();
  const [rules, setRules] = useState([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState('');

  // Delete Modal
  const [deleteModal, setDeleteModal] = useState(false);
  const [selectedRule, setSelectedRule] = useState(null);
  const [deleteNote, setDeleteNote] = useState('');
  const [actionLoading, setActionLoading] = useState(false);

  const fetchRules = async () => {
    try {
      const data = await api.getRules();
      setRules(data);
    } catch (err) {
      console.error('Failed to load rules', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchRules();
  }, []);

  const handleToggle = async (rule) => {
    if (!hasRole('admin')) return;
    const note = prompt(`Reason for ${rule.enabled ? 'disabling' : 'enabling'} rule "${rule.name}":`);
    if (note === null) return;
    try {
      await api.updateRule(rule.id, { enabled: !rule.enabled, note: note.trim() });
      fetchRules();
    } catch (err) {
      alert(`Update failed: ${err.message}`);
    }
  };

  const confirmDelete = async () => {
    if (!deleteNote || deleteNote.trim().length < 3) {
      alert('Please provide a reason for deleting this rule');
      return;
    }
    setActionLoading(true);
    try {
      await api.deleteRule(selectedRule.id, deleteNote.trim());
      setDeleteModal(false);
      fetchRules();
    } catch (err) {
      alert(`Delete failed: ${err.message}`);
    } finally {
      setActionLoading(false);
    }
  };

  const filteredRules = rules.filter((r) => {
    if (!searchQuery) return true;
    const q = searchQuery.toLowerCase();
    return (
      r.name.toLowerCase().includes(q) ||
      r.id.toLowerCase().includes(q) ||
      r.pattern.toLowerCase().includes(q) ||
      r.tags?.some((t) => t.toLowerCase().includes(q))
    );
  });

  return (
    <div className="space-y-6 animate-in fade-in duration-300">
      
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight">DLP Inspection Rules</h1>
          <p className="text-sm text-slate-400 mt-1">
            Active patterns, confidence thresholds, and automated spooler actions
          </p>
        </div>

        <div className="flex items-center space-x-3">
          {/* Search Bar */}
          <div className="relative w-full sm:w-72">
            <Search className="w-4 h-4 text-slate-400 absolute left-3.5 top-1/2 -translate-y-1/2 pointer-events-none" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search rules, tags, regex..."
              className="w-full pl-10 pr-4 py-2 bg-surface-850 border border-slate-700 rounded-xl text-sm text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
          </div>

          {/* New Rule Button */}
          {hasRole('admin') && (
            <Link
              to="/rules/new"
              className="px-4 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-sm font-semibold shadow-lg shadow-indigo-600/30 flex items-center space-x-2 transition-all whitespace-nowrap"
            >
              <Plus className="w-4 h-4" />
              <span>Create Rule</span>
            </Link>
          )}
        </div>
      </div>

      {/* Rules Grid */}
      <div className="glass-card rounded-2xl border border-slate-800 overflow-hidden shadow-lg">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="bg-surface-850 text-slate-400 uppercase tracking-wider font-semibold border-b border-slate-800 text-xs">
              <tr>
                <th className="px-5 py-3.5">Status</th>
                <th className="px-5 py-3.5">Rule Name & ID</th>
                <th className="px-5 py-3.5">Pattern</th>
                <th className="px-5 py-3.5">Action</th>
                <th className="px-5 py-3.5">Severity</th>
                <th className="px-5 py-3.5">Validator</th>
                <th className="px-5 py-3.5">Min</th>
                <th className="px-5 py-3.5">Tags</th>
                <th className="px-5 py-3.5 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60">
              {filteredRules.length > 0 ? (
                filteredRules.map((rule) => (
                  <tr key={rule.id} className="hover:bg-slate-800/30 transition-colors">
                    
                    {/* Status Toggle */}
                    <td className="px-5 py-4">
                      <button
                        onClick={() => handleToggle(rule)}
                        disabled={!hasRole('admin')}
                        title={rule.enabled ? 'Click to disable' : 'Click to enable'}
                        className={`inline-flex items-center px-3 py-1 rounded-full text-xs font-bold border transition-colors ${
                          rule.enabled
                            ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30 hover:bg-emerald-500/20'
                            : 'bg-slate-800 text-slate-500 border-slate-700 hover:bg-slate-700'
                        }`}
                      >
                        {rule.enabled ? 'ACTIVE' : 'OFF'}
                      </button>
                    </td>

                    {/* Name & ID */}
                    <td className="px-5 py-4">
                      <div className="font-bold text-white text-base">{rule.name}</div>
                      <div className="text-xs text-slate-400 font-mono mt-0.5">{rule.id}</div>
                    </td>

                    {/* Pattern */}
                    <td className="px-5 py-4 font-mono text-slate-300 max-w-xs truncate" title={rule.pattern}>
                      {rule.pattern}
                    </td>

                    {/* Action */}
                    <td className="px-5 py-4 font-mono">
                      <span className={`px-2.5 py-1 rounded-md text-xs font-bold uppercase ${
                        rule.action === 'hold'
                          ? 'bg-amber-500/10 text-amber-400 border border-amber-500/30'
                          : rule.action === 'block'
                          ? 'bg-rose-500/10 text-rose-400 border border-rose-500/30'
                          : 'bg-blue-500/10 text-blue-400 border border-blue-500/30'
                      }`}>
                        {rule.action}
                      </span>
                    </td>

                    {/* Severity */}
                    <td className="px-5 py-4 font-mono text-slate-200 font-bold text-base">{rule.severity}</td>

                    {/* Validator */}
                    <td className="px-5 py-4 font-mono text-indigo-400 font-semibold">{rule.validator || 'none'}</td>

                    {/* Min Matches */}
                    <td className="px-5 py-4 font-mono text-slate-300">{rule.min_count}</td>

                    {/* Tags */}
                    <td className="px-5 py-4">
                      <div className="flex flex-wrap gap-1.5 max-w-xs">
                        {rule.tags?.map((t) => (
                          <span key={t} className="px-2 py-0.5 rounded-md bg-surface-900 text-slate-300 border border-slate-700/60 text-xs font-mono">
                            {t}
                          </span>
                        ))}
                      </div>
                    </td>

                    {/* Row Actions */}
                    <td className="px-5 py-4 text-right space-x-3 whitespace-nowrap">
                      {hasRole('admin') && (
                        <>
                          <Link
                            to={`/rules/${rule.id}/edit`}
                            className="p-1.5 text-slate-400 hover:text-indigo-400 inline-block transition-colors"
                            title="Edit rule & test fixtures"
                          >
                            <Edit3 className="w-4 h-4" />
                          </Link>
                          <button
                            onClick={() => { setSelectedRule(rule); setDeleteNote(''); setDeleteModal(true); }}
                            className="p-1.5 text-slate-400 hover:text-rose-400 inline-block transition-colors"
                            title="Delete rule"
                          >
                            <Trash2 className="w-4 h-4" />
                          </button>
                        </>
                      )}
                    </td>

                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan="9" className="px-5 py-10 text-center text-slate-500 text-sm">
                    {loading ? 'Loading rules...' : 'No rules configured. Click "Create Rule" to add one.'}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Delete Rule Confirmation Modal */}
      <Modal
        isOpen={deleteModal}
        onClose={() => setDeleteModal(false)}
        title={`Delete Rule: ${selectedRule?.name}`}
        maxWidth="max-w-xl"
      >
        <div className="space-y-4 text-sm">
          <div className="p-3.5 rounded-xl bg-rose-500/10 border border-rose-500/30 flex items-start space-x-2.5 text-rose-400 text-sm">
            <AlertCircle className="w-5 h-5 shrink-0 mt-0.5" />
            <span>Deleting a rule is an audited event. Enter a justification for this removal.</span>
          </div>

          <div>
            <label className="block font-medium text-slate-400 mb-1.5">Reason for Deletion</label>
            <input
              type="text"
              value={deleteNote}
              onChange={(e) => setDeleteNote(e.target.value)}
              placeholder="e.g., Obsolete rule, consolidated into corporate-pack"
              className="w-full px-3.5 py-2.5 bg-surface-850 border border-slate-700 rounded-xl text-sm text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-rose-500"
              autoFocus
            />
          </div>

          <div className="flex justify-end space-x-3 pt-3">
            <button
              onClick={() => setDeleteModal(false)}
              className="px-4 py-2.5 rounded-xl font-medium text-slate-400 hover:text-white hover:bg-slate-800 transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={confirmDelete}
              disabled={actionLoading}
              className="px-5 py-2.5 rounded-xl font-semibold text-white bg-rose-600 hover:bg-rose-500 shadow-lg shadow-rose-600/30 transition-all text-sm"
            >
              {actionLoading ? 'Deleting...' : 'Confirm Delete'}
            </button>
          </div>
        </div>
      </Modal>

    </div>
  );
}
