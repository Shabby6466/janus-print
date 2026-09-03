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
          <h1 className="text-xl font-bold text-white tracking-tight">DLP Inspection Rules</h1>
          <p className="text-xs text-slate-400 mt-0.5">
            Active patterns, confidence thresholds, and automated spooler actions
          </p>
        </div>

        <div className="flex items-center space-x-3">
          {/* Search Bar */}
          <div className="relative w-full sm:w-64">
            <Search className="w-4 h-4 text-slate-400 absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search rules, tags, regex..."
              className="w-full pl-9 pr-3 py-1.5 bg-surface-850 border border-slate-700 rounded-lg text-xs text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
          </div>

          {/* New Rule Button */}
          {hasRole('admin') && (
            <Link
              to="/rules/new"
              className="px-3.5 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold shadow-lg shadow-indigo-600/30 flex items-center space-x-1.5 transition-all whitespace-nowrap"
            >
              <Plus className="w-3.5 h-3.5" />
              <span>Create Rule</span>
            </Link>
          )}
        </div>
      </div>

      {/* Rules Grid */}
      <div className="glass-card rounded-xl border border-slate-800 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="bg-surface-850 text-slate-400 uppercase tracking-wider font-semibold border-b border-slate-800">
              <tr>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3">Rule Name & ID</th>
                <th className="px-4 py-3">Pattern</th>
                <th className="px-4 py-3">Action</th>
                <th className="px-4 py-3">Severity</th>
                <th className="px-4 py-3">Validator</th>
                <th className="px-4 py-3">Min</th>
                <th className="px-4 py-3">Tags</th>
                <th className="px-4 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60">
              {filteredRules.length > 0 ? (
                filteredRules.map((rule) => (
                  <tr key={rule.id} className="hover:bg-slate-800/30 transition-colors">
                    
                    {/* Status Toggle */}
                    <td className="px-4 py-3">
                      <button
                        onClick={() => handleToggle(rule)}
                        disabled={!hasRole('admin')}
                        title={rule.enabled ? 'Click to disable' : 'Click to enable'}
                        className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] font-semibold border transition-colors ${
                          rule.enabled
                            ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30 hover:bg-emerald-500/20'
                            : 'bg-slate-800 text-slate-500 border-slate-700 hover:bg-slate-700'
                        }`}
                      >
                        {rule.enabled ? 'ACTIVE' : 'OFF'}
                      </button>
                    </td>

                    {/* Name & ID */}
                    <td className="px-4 py-3">
                      <div className="font-semibold text-white">{rule.name}</div>
                      <div className="text-[10px] text-slate-400 font-mono">{rule.id}</div>
                    </td>

                    {/* Pattern */}
                    <td className="px-4 py-3 font-mono text-slate-300 max-w-xs truncate" title={rule.pattern}>
                      {rule.pattern}
                    </td>

                    {/* Action */}
                    <td className="px-4 py-3 font-mono">
                      <span className={`px-2 py-0.5 rounded text-[11px] font-semibold uppercase ${
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
                    <td className="px-4 py-3 font-mono text-slate-300 font-bold">{rule.severity}</td>

                    {/* Validator */}
                    <td className="px-4 py-3 font-mono text-indigo-400">{rule.validator || 'none'}</td>

                    {/* Min Matches */}
                    <td className="px-4 py-3 font-mono text-slate-400">{rule.min_count}</td>

                    {/* Tags */}
                    <td className="px-4 py-3">
                      <div className="flex flex-wrap gap-1 max-w-xs">
                        {rule.tags?.map((t) => (
                          <span key={t} className="px-1.5 py-0.5 rounded bg-surface-900 text-slate-400 border border-slate-700/60 text-[10px] font-mono">
                            {t}
                          </span>
                        ))}
                      </div>
                    </td>

                    {/* Row Actions */}
                    <td className="px-4 py-3 text-right space-x-2 whitespace-nowrap">
                      {hasRole('admin') && (
                        <>
                          <Link
                            to={`/rules/${rule.id}/edit`}
                            className="p-1 text-slate-400 hover:text-indigo-400 inline-block"
                            title="Edit rule & test fixtures"
                          >
                            <Edit3 className="w-3.5 h-3.5" />
                          </Link>
                          <button
                            onClick={() => { setSelectedRule(rule); setDeleteNote(''); setDeleteModal(true); }}
                            className="p-1 text-slate-400 hover:text-rose-400 inline-block"
                            title="Delete rule"
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        </>
                      )}
                    </td>

                  </tr>
                ))
              ) : (
                <tr>
                  <td colSpan="9" className="px-4 py-8 text-center text-slate-500">
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
      >
        <div className="space-y-4">
          <div className="p-3 rounded-lg bg-rose-500/10 border border-rose-500/30 flex items-start space-x-2 text-rose-400 text-xs">
            <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
            <span>Deleting a rule is an audited event. Enter a justification for this removal.</span>
          </div>

          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">Reason for Deletion</label>
            <input
              type="text"
              value={deleteNote}
              onChange={(e) => setDeleteNote(e.target.value)}
              placeholder="e.g., Obsolete rule, consolidated into corporate-pack"
              className="w-full px-3 py-2 bg-surface-850 border border-slate-700 rounded-lg text-sm text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-rose-500"
              autoFocus
            />
          </div>

          <div className="flex justify-end space-x-2 pt-2">
            <button
              onClick={() => setDeleteModal(false)}
              className="px-4 py-2 rounded-lg text-xs font-medium text-slate-400 hover:text-white hover:bg-slate-800 transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={confirmDelete}
              disabled={actionLoading}
              className="px-4 py-2 rounded-lg text-xs font-semibold text-white bg-rose-600 hover:bg-rose-500 shadow-lg shadow-rose-600/30 transition-all"
            >
              {actionLoading ? 'Deleting...' : 'Confirm Delete'}
            </button>
          </div>
        </div>
      </Modal>

    </div>
  );
}
