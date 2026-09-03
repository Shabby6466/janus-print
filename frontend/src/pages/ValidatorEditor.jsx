import React, { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { ArrowLeft, Save, Binary, Play, Sparkles } from 'lucide-react';
import { api } from '../api';
import { Modal } from '../components/Modal';

export function ValidatorEditor() {
  const { id } = useParams();
  const isEditing = Boolean(id);
  const navigate = useNavigate();

  const [loading, setLoading] = useState(isEditing);
  const [saveModal, setSaveModal] = useState(false);
  const [saveNote, setSaveNote] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  // Form State
  const [formData, setFormData] = useState({
    id: '',
    name: '',
    description: '',
    kind: 'weighted_mod11',
    paramsJson: JSON.stringify({ weights: [7, 6, 5, 4, 3, 2], modulus: 11 }, null, 2),
    fixtures: {
      pass: '876543-2',
      fail: '876543-9\n123456-0',
    },
  });

  // Tester state
  const [testSample, setTestSample] = useState('876543-2');
  const [testResult, setTestResult] = useState(null);
  const [testing, setTesting] = useState(false);

  useEffect(() => {
    if (isEditing) {
      const loadVal = async () => {
        try {
          const val = await api.getValidator(id);
          setFormData({
            id: val.id,
            name: val.name,
            description: val.description || '',
            kind: val.kind,
            paramsJson: JSON.stringify(val.params || {}, null, 2),
            fixtures: {
              pass: Array.isArray(val.fixtures?.pass) ? val.fixtures.pass.join('\n') : '',
              fail: Array.isArray(val.fixtures?.fail) ? val.fixtures.fail.join('\n') : '',
            },
          });
        } catch (err) {
          setError(err.message || 'Failed to load validator');
        } finally {
          setLoading(false);
        }
      };
      loadVal();
    }
  }, [id, isEditing]);

  const runTest = async () => {
    if (!testSample) return;
    setTesting(true);
    try {
      let params = {};
      try {
        params = JSON.parse(formData.paramsJson);
      } catch {
        throw new Error('Invalid JSON in parameters field');
      }
      const result = await api.tryValidator(formData.kind, params, testSample.trim());
      setTestResult(result);
    } catch (err) {
      setTestResult({ error: err.message });
    } finally {
      setTesting(false);
    }
  };

  const handleSave = async () => {
    if (!saveNote || saveNote.trim().length < 3) {
      alert('Please provide a reason for this change');
      return;
    }
    setSaving(true);
    try {
      let params = {};
      try {
        params = JSON.parse(formData.paramsJson);
      } catch {
        throw new Error('Invalid JSON in parameters field');
      }

      const payload = {
        id: formData.id,
        name: formData.name,
        description: formData.description,
        kind: formData.kind,
        params,
        fixtures: {
          pass: formData.fixtures.pass.split('\n').map((s) => s.trim()).filter(Boolean),
          fail: formData.fixtures.fail.split('\n').map((s) => s.trim()).filter(Boolean),
        },
        note: saveNote.trim(),
      };

      if (isEditing) {
        await api.updateValidator(id, payload);
      } else {
        await api.createValidator(payload);
      }
      navigate('/validators');
    } catch (err) {
      alert(`Save failed: ${err.message}`);
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return <div className="py-24 text-center text-slate-400 font-mono text-sm">Loading validator editor...</div>;
  }

  return (
    <div className="space-y-6 animate-in fade-in duration-300">
      
      {/* Top Header */}
      <div className="flex items-center justify-between">
        <button
          onClick={() => navigate('/validators')}
          className="inline-flex items-center space-x-1.5 text-xs text-slate-400 hover:text-white transition-colors"
        >
          <ArrowLeft className="w-4 h-4" />
          <span>Back to Validators</span>
        </button>

        <button
          onClick={() => { setSaveNote(''); setSaveModal(true); }}
          className="px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold shadow-lg shadow-indigo-600/30 flex items-center space-x-1.5 transition-all"
        >
          <Save className="w-4 h-4" />
          <span>{isEditing ? 'Save Changes' : 'Create Validator'}</span>
        </button>
      </div>

      {error && (
        <div className="p-3 rounded-lg bg-rose-500/10 border border-rose-500/30 text-rose-400 text-xs">
          {error}
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        
        {/* Left: Configuration */}
        <div className="glass-card rounded-xl p-6 border border-slate-800 space-y-4">
          <h2 className="text-sm font-semibold text-white flex items-center space-x-2 border-b border-slate-800 pb-3">
            <Binary className="w-4 h-4 text-indigo-400" />
            <span>Algorithm Definition</span>
          </h2>

          <div className="space-y-3 text-xs">
            <div>
              <label className="block text-slate-300 font-medium mb-1">Validator ID</label>
              <input
                type="text"
                disabled={isEditing}
                value={formData.id}
                onChange={(e) => setFormData({ ...formData, id: e.target.value })}
                placeholder="custom-employee-id"
                className="w-full px-3 py-2 bg-surface-850 border border-slate-700 rounded-lg text-white font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500 disabled:opacity-50"
              />
            </div>

            <div>
              <label className="block text-slate-300 font-medium mb-1">Validator Name</label>
              <input
                type="text"
                value={formData.name}
                onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                placeholder="Custom Employee ID Checksum"
                className="w-full px-3 py-2 bg-surface-850 border border-slate-700 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
            </div>

            <div>
              <label className="block text-slate-300 font-medium mb-1">Description</label>
              <input
                type="text"
                value={formData.description}
                onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                placeholder="Verifies 7-digit badge numbers with weighted mod-11 check digit"
                className="w-full px-3 py-2 bg-surface-850 border border-slate-700 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
            </div>

            <div>
              <label className="block text-slate-300 font-medium mb-1">Mathematical Kind</label>
              <select
                value={formData.kind}
                onChange={(e) => setFormData({ ...formData, kind: e.target.value })}
                className="w-full px-3 py-2 bg-surface-850 border border-slate-700 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-indigo-500 font-mono"
              >
                <option value="weighted_mod11">weighted_mod11 (Position weights & Modulo-11)</option>
                <option value="damm">damm (Anti-transposition digit)</option>
                <option value="entropy">entropy (Shannon randomness gate)</option>
              </select>
            </div>

            <div>
              <label className="block text-slate-300 font-medium mb-1">Parameters (JSON)</label>
              <textarea
                rows={5}
                value={formData.paramsJson}
                onChange={(e) => setFormData({ ...formData, paramsJson: e.target.value })}
                className="w-full px-3 py-2 bg-surface-850 border border-slate-700 rounded-lg text-white font-mono text-xs focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
            </div>
          </div>
        </div>

        {/* Right: Fixtures & Live Tester */}
        <div className="space-y-6">
          
          {/* Mandatory Test Fixtures */}
          <div className="glass-card rounded-xl p-6 border border-slate-800 space-y-4">
            <h2 className="text-sm font-semibold text-white flex items-center space-x-2 border-b border-slate-800 pb-3">
              <Sparkles className="w-4 h-4 text-indigo-400" />
              <span>Mandatory Proof Fixtures</span>
            </h2>
            <p className="text-[11px] text-slate-400">
              At least one PASS example and one FAIL example are required to verify the math before saving.
            </p>

            <div className="space-y-3 text-xs">
              <div>
                <label className="block text-emerald-400 font-semibold mb-1">Examples that Must PASS</label>
                <textarea
                  rows={2}
                  value={formData.fixtures.pass}
                  onChange={(e) => setFormData({ ...formData, fixtures: { ...formData.fixtures, pass: e.target.value } })}
                  placeholder="876543-2"
                  className="w-full px-3 py-2 bg-surface-850 border border-slate-700 rounded-lg text-white font-mono text-xs focus:outline-none focus:ring-2 focus:ring-emerald-500"
                />
              </div>

              <div>
                <label className="block text-rose-400 font-semibold mb-1">Examples that Must FAIL</label>
                <textarea
                  rows={2}
                  value={formData.fixtures.fail}
                  onChange={(e) => setFormData({ ...formData, fixtures: { ...formData.fixtures, fail: e.target.value } })}
                  placeholder="876543-9"
                  className="w-full px-3 py-2 bg-surface-850 border border-slate-700 rounded-lg text-white font-mono text-xs focus:outline-none focus:ring-2 focus:ring-rose-500"
                />
              </div>
            </div>
          </div>

          {/* Quick Tester */}
          <div className="glass-card rounded-xl p-6 border border-slate-800 space-y-4">
            <div className="flex items-center justify-between border-b border-slate-800 pb-3">
              <h2 className="text-sm font-semibold text-white flex items-center space-x-2">
                <Play className="w-4 h-4 text-indigo-400" />
                <span>Test Validator Algorithm</span>
              </h2>

              <button
                onClick={runTest}
                disabled={testing}
                className="px-3 py-1 rounded bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold shadow-md transition-all"
              >
                {testing ? 'Testing...' : 'Test Sample'}
              </button>
            </div>

            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1">Sample String</label>
              <input
                type="text"
                value={testSample}
                onChange={(e) => setTestSample(e.target.value)}
                placeholder="876543-2"
                className="w-full px-3 py-2 bg-surface-850 border border-slate-700 rounded-lg text-white text-xs font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
            </div>

            {testResult && (
              <div className="p-3 rounded-xl bg-surface-850 border border-slate-800 text-xs font-mono">
                {testResult.error ? (
                  <span className="text-rose-400">{testResult.error}</span>
                ) : (
                  <div className="flex items-center justify-between">
                    <span className="text-slate-300">Result:</span>
                    <span className={`font-bold px-2 py-0.5 rounded ${
                      testResult.passes ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/30' : 'bg-rose-500/10 text-rose-400 border border-rose-500/30'
                    }`}>
                      {testResult.passes ? 'VALID (PASSED CHECKSUM)' : 'INVALID (FAILED CHECKSUM)'}
                    </span>
                  </div>
                )}
              </div>
            )}
          </div>

        </div>

      </div>

      {/* Save Modal */}
      <Modal
        isOpen={saveModal}
        onClose={() => setSaveModal(false)}
        title={isEditing ? 'Save Validator' : 'Create Custom Validator'}
      >
        <div className="space-y-4">
          <p className="text-xs text-slate-300">
            Provide a permanent audit justification for registering or modifying this checksum algorithm.
          </p>

          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">Reason for change</label>
            <input
              type="text"
              value={saveNote}
              onChange={(e) => setSaveNote(e.target.value)}
              placeholder="e.g., Added employee ID check algorithm per HR spec"
              className="w-full px-3 py-2 bg-surface-850 border border-slate-700 rounded-lg text-sm text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-indigo-500"
              autoFocus
            />
          </div>

          <div className="flex justify-end space-x-2 pt-2">
            <button
              onClick={() => setSaveModal(false)}
              className="px-4 py-2 rounded-lg text-xs font-medium text-slate-400 hover:text-white hover:bg-slate-800 transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={handleSave}
              disabled={saving}
              className="px-4 py-2 rounded-lg text-xs font-semibold text-white bg-indigo-600 hover:bg-indigo-500 shadow-lg shadow-indigo-600/30 transition-all"
            >
              {saving ? 'Validating & Saving...' : 'Confirm & Save'}
            </button>
          </div>
        </div>
      </Modal>

    </div>
  );
}
