import React, { useState, useEffect } from 'react';
import { Printer, RefreshCw, Plus, Trash2, CheckCircle2, AlertTriangle, ShieldCheck } from 'lucide-react';
import { api } from '../api';
import { Modal } from '../components/Modal';
import { useAuth } from '../context/AuthContext';

export function Printers() {
  const { hasRole } = useAuth();
  const [printers, setPrinters] = useState([]);
  const [loading, setLoading] = useState(true);

  // Policy Patch Modal
  const [modalOpen, setModalOpen] = useState(false);
  const [selectedPrinter, setSelectedPrinter] = useState(null);
  const [patchField, setPatchField] = useState({});
  const [patchNote, setPatchNote] = useState('');
  const [actionLoading, setActionLoading] = useState(false);

  // Add Printer Modal
  const [addModal, setAddModal] = useState(false);
  const [newPrinter, setNewPrinter] = useState({
    name: '',
    device_uri: '',
    description: '',
    location: '',
    mode: 'enforce',
    fail_mode: 'open',
    note: '',
  });

  const fetchPrinters = async () => {
    try {
      const data = await api.getPrinters();
      setPrinters(data);
    } catch (err) {
      console.error('Failed to load printers', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchPrinters();
  }, []);

  const handleModeChange = (printer, newMode) => {
    if (!hasRole('admin')) return;
    setSelectedPrinter(printer);
    setPatchField({ mode: newMode });
    setPatchNote('');
    setModalOpen(true);
  };

  const handleFailModeChange = (printer, newFailMode) => {
    if (!hasRole('admin')) return;
    setSelectedPrinter(printer);
    setPatchField({ fail_mode: newFailMode });
    setPatchNote('');
    setModalOpen(true);
  };

  const submitPatch = async () => {
    if (!patchNote || patchNote.trim().length < 3) {
      alert('Please provide a reason for changing printer queue policy');
      return;
    }
    setActionLoading(true);
    try {
      await api.updatePrinter(selectedPrinter.name, {
        ...patchField,
        note: patchNote.trim(),
      });
      setModalOpen(false);
      fetchPrinters();
    } catch (err) {
      alert(`Policy update failed: ${err.message}`);
    } finally {
      setActionLoading(false);
    }
  };

  const handleAdopt = async (name) => {
    if (!hasRole('admin')) return;
    try {
      await api.adoptPrinter(name);
      fetchPrinters();
    } catch (err) {
      alert(`Adoption failed: ${err.message}`);
    }
  };

  const handleAddPrinter = async (e) => {
    e.preventDefault();
    if (!newPrinter.name || !newPrinter.device_uri) {
      alert('Queue Name and Device URI are required');
      return;
    }
    setActionLoading(true);
    try {
      await api.createPrinter({
        ...newPrinter,
        note: newPrinter.note || 'Added printer queue from React console',
      });
      setAddModal(false);
      setNewPrinter({
        name: '',
        device_uri: '',
        description: '',
        location: '',
        mode: 'enforce',
        fail_mode: 'open',
        note: '',
      });
      fetchPrinters();
    } catch (err) {
      alert(`Failed to create printer queue: ${err.message}`);
    } finally {
      setActionLoading(false);
    }
  };

  return (
    <div className="space-y-8 animate-in fade-in duration-300">
      
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-white tracking-tight">CUPS Printer Queues</h1>
          <p className="text-sm text-slate-400 mt-1">
            Manage interception policies, scan modes, and fail-safe behaviors across office printers
          </p>
        </div>

        <div className="flex items-center space-x-3">
          {hasRole('admin') && (
            <button
              onClick={() => setAddModal(true)}
              className="px-4 py-2 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white text-sm font-semibold shadow-lg shadow-indigo-600/30 flex items-center space-x-2 transition-all"
            >
              <Plus className="w-4 h-4" />
              <span>Add Printer Queue</span>
            </button>
          )}

          <button
            onClick={fetchPrinters}
            className="px-4 py-2 rounded-xl bg-surface-850 hover:bg-slate-700 text-slate-300 border border-slate-700 text-sm font-medium flex items-center space-x-2 transition-colors"
          >
            <RefreshCw className="w-4 h-4" />
            <span>Refresh</span>
          </button>
        </div>
      </div>

      {/* Printer Cards Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {printers.map((p) => {
          const isManaged = Boolean(p.managed !== false && p.device_uri);
          return (
            <div key={p.name} className="glass-card rounded-2xl p-6 border border-slate-800 space-y-5 flex flex-col justify-between shadow-lg">
              
              {/* Card Header */}
              <div>
                <div className="flex items-start justify-between">
                  <div className="flex items-center space-x-3.5">
                    <div className="p-2.5 rounded-xl bg-indigo-600/15 border border-indigo-500/30 text-indigo-400">
                      <Printer className="w-6 h-6" />
                    </div>
                    <div>
                      <h3 className="text-lg font-bold text-white tracking-tight">{p.name}</h3>
                      <div className="text-xs font-mono text-slate-400 truncate max-w-[220px]" title={p.device_uri}>
                        {p.device_uri || 'No backend URI'}
                      </div>
                    </div>
                  </div>

                  <span className={`px-2.5 py-1 rounded-full text-xs font-bold uppercase tracking-wider ${
                    isManaged
                      ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/30'
                      : 'bg-amber-500/10 text-amber-400 border border-amber-500/30'
                  }`}>
                    {isManaged ? 'MANAGED' : 'UNADOPTED'}
                  </span>
                </div>

                {/* Settings list */}
                {isManaged ? (
                  <dl className="mt-5 space-y-3.5 text-sm">
                    <div className="flex items-center justify-between">
                      <dt className="text-slate-400 font-medium">Scan Mode</dt>
                      <dd>
                        <select
                          disabled={!hasRole('admin')}
                          value={p.mode || (p.deep_scan_required ? 'enforce' : 'monitor')}
                          onChange={(e) => handleModeChange(p, e.target.value)}
                          className="px-3 py-1.5 bg-surface-850 border border-slate-700 rounded-lg text-sm text-white focus:outline-none focus:ring-2 focus:ring-indigo-500 font-semibold disabled:opacity-50 cursor-pointer"
                        >
                          <option value="enforce">Enforce (Strict Hold)</option>
                          <option value="monitor">Monitor (Log Only)</option>
                          <option value="disabled">Disabled (Bypass)</option>
                        </select>
                      </dd>
                    </div>

                    <div className="flex items-center justify-between">
                      <dt className="text-slate-400 font-medium">Fail Mode</dt>
                      <dd>
                        <select
                          disabled={!hasRole('admin')}
                          value={p.fail_mode || 'open'}
                          onChange={(e) => handleFailModeChange(p, e.target.value)}
                          className="px-3 py-1.5 bg-surface-850 border border-slate-700 rounded-lg text-sm text-white focus:outline-none focus:ring-2 focus:ring-indigo-500 font-mono disabled:opacity-50 cursor-pointer"
                        >
                          <option value="open">open (Print on outage)</option>
                          <option value="closed">closed (Hold on outage)</option>
                        </select>
                      </dd>
                    </div>

                    <div className="flex items-center justify-between">
                      <dt className="text-slate-400 font-medium">Unreadable Page Policy</dt>
                      <dd className="font-mono text-slate-300 font-semibold uppercase">{p.on_unreadable || 'hold'}</dd>
                    </div>

                    <div className="flex items-center justify-between">
                      <dt className="text-slate-400 font-medium">Deep Scan Required</dt>
                      <dd className={`font-mono font-bold text-sm ${p.deep_scan_required ? 'text-emerald-400' : 'text-slate-500'}`}>
                        {p.deep_scan_required ? 'YES' : 'NO'}
                      </dd>
                    </div>
                  </dl>
                ) : (
                  <div className="mt-5 p-4 rounded-xl bg-amber-500/10 border border-amber-500/30 text-xs text-amber-300 space-y-3">
                    <p className="text-sm">This queue exists in CUPS but is not managed under Janus DLP policy.</p>
                    {hasRole('admin') && (
                      <button
                        onClick={() => handleAdopt(p.name)}
                        className="w-full py-2.5 rounded-xl bg-amber-600 hover:bg-amber-500 text-white font-semibold text-sm transition-colors shadow-md"
                      >
                        Adopt under Janus DLP
                      </button>
                    )}
                  </div>
                )}
              </div>

              <div className="pt-4 border-t border-slate-800/80 text-xs text-slate-400 flex justify-between">
                <span>Updated: {p.updated_at ? new Date(p.updated_at).toLocaleDateString() : 'initial'}</span>
                <span>By: {p.updated_by || 'system'}</span>
              </div>

            </div>
          );
        })}
      </div>

      {/* Add Printer Modal */}
      <Modal
        isOpen={addModal}
        onClose={() => setAddModal(false)}
        title="Add New Printer Queue"
        maxWidth="max-w-xl"
      >
        <form onSubmit={handleAddPrinter} className="space-y-4 text-sm">
          <div>
            <label className="block text-slate-300 font-medium mb-1.5">Queue Name (e.g. office-laser, reliance)</label>
            <input
              type="text"
              value={newPrinter.name}
              onChange={(e) => setNewPrinter({ ...newPrinter, name: e.target.value })}
              placeholder="reliance"
              className="w-full px-3.5 py-2.5 bg-surface-850 border border-slate-700 rounded-xl text-white font-mono text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              required
            />
          </div>

          <div>
            <label className="block text-slate-300 font-medium mb-1.5">Device URI (Physical printer address)</label>
            <input
              type="text"
              value={newPrinter.device_uri}
              onChange={(e) => setNewPrinter({ ...newPrinter, device_uri: e.target.value })}
              placeholder="ipp://10.0.1.80/ipp/print or socket://10.0.1.80:9100"
              className="w-full px-3.5 py-2.5 bg-surface-850 border border-slate-700 rounded-xl text-white font-mono text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              required
            />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-slate-300 font-medium mb-1.5">Description</label>
              <input
                type="text"
                value={newPrinter.description}
                onChange={(e) => setNewPrinter({ ...newPrinter, description: e.target.value })}
                placeholder="Executive Floor Color Laser"
                className="w-full px-3.5 py-2.5 bg-surface-850 border border-slate-700 rounded-xl text-white text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
            </div>

            <div>
              <label className="block text-slate-300 font-medium mb-1.5">Location</label>
              <input
                type="text"
                value={newPrinter.location}
                onChange={(e) => setNewPrinter({ ...newPrinter, location: e.target.value })}
                placeholder="HQ Floor 3"
                className="w-full px-3.5 py-2.5 bg-surface-850 border border-slate-700 rounded-xl text-white text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="block text-slate-300 font-medium mb-1.5">Scan Mode</label>
              <select
                value={newPrinter.mode}
                onChange={(e) => setNewPrinter({ ...newPrinter, mode: e.target.value })}
                className="w-full px-3.5 py-2.5 bg-surface-850 border border-slate-700 rounded-xl text-white text-sm focus:outline-none focus:ring-2 focus:ring-indigo-500 font-medium"
              >
                <option value="enforce">Enforce (Strict Hold)</option>
                <option value="monitor">Monitor (Log Only)</option>
              </select>
            </div>

            <div>
              <label className="block text-slate-300 font-medium mb-1.5">Fail Mode</label>
              <select
                value={newPrinter.fail_mode}
                onChange={(e) => setNewPrinter({ ...newPrinter, fail_mode: e.target.value })}
                className="w-full px-3.5 py-2.5 bg-surface-850 border border-slate-700 rounded-xl text-white text-sm font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500"
              >
                <option value="open">open (Print on outage)</option>
                <option value="closed">closed (Hold on outage)</option>
              </select>
            </div>
          </div>

          <div className="flex justify-end space-x-3 pt-4">
            <button
              type="button"
              onClick={() => setAddModal(false)}
              className="px-4 py-2.5 rounded-xl font-medium text-slate-400 hover:text-white hover:bg-slate-800 transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={actionLoading}
              className="px-5 py-2.5 rounded-xl font-semibold text-white bg-indigo-600 hover:bg-indigo-500 shadow-lg shadow-indigo-600/30 transition-all text-sm"
            >
              {actionLoading ? 'Creating Queue...' : 'Create Queue'}
            </button>
          </div>
        </form>
      </Modal>

      {/* Policy Change Reason Modal */}
      <Modal
        isOpen={modalOpen}
        onClose={() => setModalOpen(false)}
        title={`Change Policy for: ${selectedPrinter?.name}`}
        maxWidth="max-w-xl"
      >
        <div className="space-y-4 text-sm">
          <p className="text-slate-300">
            Printer policy modifications alter security enforcement for all connected workstations. Enter a permanent audit justification.
          </p>

          <div>
            <label className="block font-medium text-slate-400 mb-1.5">Reason for Policy Change</label>
            <input
              type="text"
              value={patchNote}
              onChange={(e) => setPatchNote(e.target.value)}
              placeholder="e.g., Switching to Enforce mode for strictly confidential compliance"
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
              onClick={submitPatch}
              disabled={actionLoading}
              className="px-5 py-2.5 rounded-xl font-semibold text-white bg-indigo-600 hover:bg-indigo-500 shadow-lg shadow-indigo-600/30 transition-all text-sm"
            >
              {actionLoading ? 'Updating Policy...' : 'Apply Change'}
            </button>
          </div>
        </div>
      </Modal>

    </div>
  );
}
