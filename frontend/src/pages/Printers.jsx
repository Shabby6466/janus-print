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
    <div className="space-y-6 animate-in fade-in duration-300">
      
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
        <div>
          <h1 className="text-xl font-bold text-white tracking-tight">CUPS Printer Queues</h1>
          <p className="text-xs text-slate-400 mt-0.5">
            Manage interception policies, scan modes, and fail-safe behaviors across office printers
          </p>
        </div>

        <div className="flex items-center space-x-2">
          {hasRole('admin') && (
            <button
              onClick={() => setAddModal(true)}
              className="px-3.5 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold shadow-lg shadow-indigo-600/30 flex items-center space-x-1.5 transition-all"
            >
              <Plus className="w-3.5 h-3.5" />
              <span>Add Printer Queue</span>
            </button>
          )}

          <button
            onClick={fetchPrinters}
            className="px-3 py-1.5 rounded-lg bg-surface-850 hover:bg-slate-700 text-slate-300 border border-slate-700 text-xs font-medium flex items-center space-x-1.5 transition-colors"
          >
            <RefreshCw className="w-3.5 h-3.5" />
            <span>Refresh</span>
          </button>
        </div>
      </div>

      {/* Printer Cards Grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {printers.map((p) => {
          const isManaged = Boolean(p.managed !== false && p.device_uri);
          return (
            <div key={p.name} className="glass-card rounded-xl p-5 border border-slate-800 space-y-4 flex flex-col justify-between">
              
              {/* Card Header */}
              <div>
                <div className="flex items-start justify-between">
                  <div className="flex items-center space-x-2.5">
                    <div className="p-2 rounded-lg bg-indigo-600/15 border border-indigo-500/30 text-indigo-400">
                      <Printer className="w-5 h-5" />
                    </div>
                    <div>
                      <h3 className="text-base font-bold text-white tracking-tight">{p.name}</h3>
                      <div className="text-[11px] font-mono text-slate-400 truncate max-w-[200px]" title={p.device_uri}>
                        {p.device_uri || 'No backend URI'}
                      </div>
                    </div>
                  </div>

                  <span className={`px-2 py-0.5 rounded text-[10px] font-semibold uppercase ${
                    isManaged
                      ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/30'
                      : 'bg-amber-500/10 text-amber-400 border border-amber-500/30'
                  }`}>
                    {isManaged ? 'MANAGED' : 'UNADOPTED'}
                  </span>
                </div>

                {/* Settings list */}
                {isManaged ? (
                  <dl className="mt-4 space-y-2.5 text-xs">
                    <div className="flex items-center justify-between">
                      <dt className="text-slate-400 font-medium">Scan Mode</dt>
                      <dd>
                        <select
                          disabled={!hasRole('admin')}
                          value={p.mode || (p.deep_scan_required ? 'enforce' : 'monitor')}
                          onChange={(e) => handleModeChange(p, e.target.value)}
                          className="px-2 py-1 bg-surface-850 border border-slate-700 rounded text-xs text-white focus:outline-none focus:ring-1 focus:ring-indigo-500 font-semibold disabled:opacity-50"
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
                          className="px-2 py-1 bg-surface-850 border border-slate-700 rounded text-xs text-white focus:outline-none focus:ring-1 focus:ring-indigo-500 font-mono disabled:opacity-50"
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
                      <dd className={`font-mono font-bold ${p.deep_scan_required ? 'text-emerald-400' : 'text-slate-500'}`}>
                        {p.deep_scan_required ? 'YES' : 'NO'}
                      </dd>
                    </div>
                  </dl>
                ) : (
                  <div className="mt-4 p-3 rounded-lg bg-amber-500/10 border border-amber-500/30 text-xs text-amber-300 space-y-2">
                    <p>This queue exists in CUPS but is not managed under Janus DLP policy.</p>
                    {hasRole('admin') && (
                      <button
                        onClick={() => handleAdopt(p.name)}
                        className="w-full py-1.5 rounded bg-amber-600 hover:bg-amber-500 text-white font-semibold transition-colors shadow-md"
                      >
                        Adopt under Janus DLP
                      </button>
                    )}
                  </div>
                )}
              </div>

              <div className="pt-3 border-t border-slate-800 text-[10px] text-slate-500 flex justify-between">
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
      >
        <form onSubmit={handleAddPrinter} className="space-y-4 text-xs">
          <div>
            <label className="block text-slate-300 font-medium mb-1">Queue Name (e.g. office-laser, reliance)</label>
            <input
              type="text"
              value={newPrinter.name}
              onChange={(e) => setNewPrinter({ ...newPrinter, name: e.target.value })}
              placeholder="reliance"
              className="w-full px-3 py-2 bg-surface-850 border border-slate-700 rounded-lg text-white font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500"
              required
            />
          </div>

          <div>
            <label className="block text-slate-300 font-medium mb-1">Device URI (Physical printer address)</label>
            <input
              type="text"
              value={newPrinter.device_uri}
              onChange={(e) => setNewPrinter({ ...newPrinter, device_uri: e.target.value })}
              placeholder="ipp://10.0.1.80/ipp/print or socket://10.0.1.80:9100"
              className="w-full px-3 py-2 bg-surface-850 border border-slate-700 rounded-lg text-white font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500"
              required
            />
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-slate-300 font-medium mb-1">Description</label>
              <input
                type="text"
                value={newPrinter.description}
                onChange={(e) => setNewPrinter({ ...newPrinter, description: e.target.value })}
                placeholder="Executive Floor Color Laser"
                className="w-full px-3 py-2 bg-surface-850 border border-slate-700 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
            </div>

            <div>
              <label className="block text-slate-300 font-medium mb-1">Location</label>
              <input
                type="text"
                value={newPrinter.location}
                onChange={(e) => setNewPrinter({ ...newPrinter, location: e.target.value })}
                placeholder="HQ Floor 3"
                className="w-full px-3 py-2 bg-surface-850 border border-slate-700 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-slate-300 font-medium mb-1">Scan Mode</label>
              <select
                value={newPrinter.mode}
                onChange={(e) => setNewPrinter({ ...newPrinter, mode: e.target.value })}
                className="w-full px-3 py-2 bg-surface-850 border border-slate-700 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
              >
                <option value="enforce">Enforce (Strict Hold)</option>
                <option value="monitor">Monitor (Log Only)</option>
              </select>
            </div>

            <div>
              <label className="block text-slate-300 font-medium mb-1">Fail Mode</label>
              <select
                value={newPrinter.fail_mode}
                onChange={(e) => setNewPrinter({ ...newPrinter, fail_mode: e.target.value })}
                className="w-full px-3 py-2 bg-surface-850 border border-slate-700 rounded-lg text-white font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500"
              >
                <option value="open">open (Print on outage)</option>
                <option value="closed">closed (Hold on outage)</option>
              </select>
            </div>
          </div>

          <div className="flex justify-end space-x-2 pt-2">
            <button
              type="button"
              onClick={() => setAddModal(false)}
              className="px-4 py-2 rounded-lg font-medium text-slate-400 hover:text-white hover:bg-slate-800 transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={actionLoading}
              className="px-4 py-2 rounded-lg font-semibold text-white bg-indigo-600 hover:bg-indigo-500 shadow-lg shadow-indigo-600/30 transition-all"
            >
              {actionLoading ? 'Creating...' : 'Create Queue'}
            </button>
          </div>
        </form>
      </Modal>

      {/* Policy Change Reason Modal */}
      <Modal
        isOpen={modalOpen}
        onClose={() => setModalOpen(false)}
        title={`Change Policy for: ${selectedPrinter?.name}`}
      >
        <div className="space-y-4">
          <p className="text-xs text-slate-300">
            Printer policy modifications alter security enforcement for all connected workstations. Enter a permanent audit justification.
          </p>

          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">Reason for Policy Change</label>
            <input
              type="text"
              value={patchNote}
              onChange={(e) => setPatchNote(e.target.value)}
              placeholder="e.g., Switching to Enforce mode for strictly confidential compliance"
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
              onClick={submitPatch}
              disabled={actionLoading}
              className="px-4 py-2 rounded-lg text-xs font-semibold text-white bg-indigo-600 hover:bg-indigo-500 shadow-lg shadow-indigo-600/30 transition-all"
            >
              {actionLoading ? 'Updating Policy...' : 'Apply Change'}
            </button>
          </div>
        </div>
      </Modal>

    </div>
  );
}
