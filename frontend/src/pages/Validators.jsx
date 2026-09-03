import React, { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { Plus, Binary, Lock, Trash2, Edit3, CheckCircle2 } from 'lucide-react';
import { api } from '../api';
import { Modal } from '../components/Modal';
import { useAuth } from '../context/AuthContext';

export function Validators() {
  const { hasRole } = useAuth();
  const [validators, setValidators] = useState([]);
  const [loading, setLoading] = useState(true);

  // Delete Modal
  const [deleteModal, setDeleteModal] = useState(false);
  const [selectedValidator, setSelectedValidator] = useState(null);
  const [deleteNote, setDeleteNote] = useState('');
  const [actionLoading, setActionLoading] = useState(false);

  const fetchValidators = async () => {
    try {
      const data = await api.getValidators();
      setValidators(data);
    } catch (err) {
      console.error('Failed to load validators', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchValidators();
  }, []);

  const confirmDelete = async () => {
    if (!deleteNote || deleteNote.trim().length < 3) {
      alert('Please provide a reason for deleting this validator');
      return;
    }
    setActionLoading(true);
    try {
      await api.deleteValidator(selectedValidator.id, deleteNote.trim());
      setDeleteModal(false);
      fetchValidators();
    } catch (err) {
      alert(`Delete failed: ${err.message}`);
    } finally {
      setActionLoading(false);
    }
  };

  return (
    <div className="space-y-6 animate-in fade-in duration-300">
      
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-xl font-bold text-white tracking-tight">Algorithmic Checksum Validators</h1>
          <p className="text-xs text-slate-400 mt-0.5">
            Mathematical structural checks (Luhn, Mod-97, Mod-11) that eliminate false positives
          </p>
        </div>

        {hasRole('admin') && (
          <Link
            to="/validators/new"
            className="px-3.5 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold shadow-lg shadow-indigo-600/30 flex items-center space-x-1.5 transition-all whitespace-nowrap"
          >
            <Plus className="w-3.5 h-3.5" />
            <span>New Custom Validator</span>
          </Link>
        )}
      </div>

      {/* Validators Grid */}
      <div className="glass-card rounded-xl border border-slate-800 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="bg-surface-850 text-slate-400 uppercase tracking-wider font-semibold border-b border-slate-800">
              <tr>
                <th className="px-4 py-3">Type</th>
                <th className="px-4 py-3">ID & Name</th>
                <th className="px-4 py-3">Description</th>
                <th className="px-4 py-3">Algorithm Kind</th>
                <th className="px-4 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60">
              {validators.length > 0 ? (
                validators.map((val) => (
                  <tr key={val.id} className="hover:bg-slate-800/30 transition-colors">
                    
                    {/* Builtin or Custom Badge */}
                    <td className="px-4 py-3">
                      {val.builtin ? (
                        <span className="inline-flex items-center space-x-1 px-2 py-0.5 rounded text-[10px] font-semibold bg-slate-800 text-slate-300 border border-slate-700">
                          <Lock className="w-2.5 h-2.5 text-amber-400" />
                          <span>BUILT-IN</span>
                        </span>
                      ) : (
                        <span className="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-semibold bg-indigo-500/10 text-indigo-400 border border-indigo-500/30">
                          CUSTOM
                        </span>
                      )}
                    </td>

                    {/* ID & Name */}
                    <td className="px-4 py-3">
                      <div className="font-semibold text-white">{val.name || val.id}</div>
                      <div className="text-[10px] text-slate-400 font-mono">{val.id}</div>
                    </td>

                    {/* Description */}
                    <td className="px-4 py-3 text-slate-300 max-w-sm">
                      {val.description || '-'}
                    </td>

                    {/* Algorithm Kind */}
                    <td className="px-4 py-3 font-mono text-indigo-300">
                      {val.kind}
                    </td>

                    {/* Actions */}
                    <td className="px-4 py-3 text-right space-x-2 whitespace-nowrap">
                      {!val.builtin && hasRole('admin') && (
                        <>
                          <Link
                            to={`/validators/${val.id}/edit`}
                            className="p-1 text-slate-400 hover:text-indigo-400 inline-block"
                            title="Edit validator"
                          >
                            <Edit3 className="w-3.5 h-3.5" />
                          </Link>
                          <button
                            onClick={() => { setSelectedValidator(val); setDeleteNote(''); setDeleteModal(true); }}
                            className="p-1 text-slate-400 hover:text-rose-400 inline-block"
                            title="Delete custom validator"
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
                  <td colSpan="5" className="px-4 py-8 text-center text-slate-500">
                    {loading ? 'Loading validators...' : 'No validators found.'}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Delete Confirmation Modal */}
      <Modal
        isOpen={deleteModal}
        onClose={() => setDeleteModal(false)}
        title={`Delete Custom Validator: ${selectedValidator?.name}`}
      >
        <div className="space-y-4">
          <p className="text-xs text-slate-300">
            Deleting a validator is an audited action. Rules currently using this validator must be updated first.
          </p>

          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">Reason for Deletion</label>
            <input
              type="text"
              value={deleteNote}
              onChange={(e) => setDeleteNote(e.target.value)}
              placeholder="e.g., Replaced with new national ID scheme"
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
