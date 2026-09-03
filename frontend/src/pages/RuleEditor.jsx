import React, { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { 
  ArrowLeft, 
  Save, 
  Play, 
  CheckCircle2, 
  XCircle, 
  SlidersHorizontal, 
  Sparkles, 
  AlertCircle 
} from 'lucide-react';
import { api } from '../api';
import { Modal } from '../components/Modal';

export function RuleEditor() {
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
    pattern: '',
    action: 'hold',
    severity: 7,
    validator: 'none',
    validator_weight: 0.3,
    base_confidence: 0.85,
    threshold: 0.75,
    min_count: 1,
    ignore_case: true,
    sample_prefix: 4,
    sample_suffix: 4,
    tags: 'confidential, corporate',
    enabled: true,
    context: {
      terms: '',
      window: 50,
      boost: 0.15,
      required: false,
    },
    fixtures: {
      positive: '',
      negative: '',
    },
  });

  // Try Tester State
  const [sampleText, setSampleText] = useState('This document is STRICTLY CONFIDENTIAL and not for public release.');
  const [testResult, setTestResult] = useState(null);
  const [testing, setTesting] = useState(false);

  useEffect(() => {
    if (isEditing) {
      const loadRule = async () => {
        try {
          const rule = await api.getRule(id);
          setFormData({
            id: rule.id,
            name: rule.name,
            description: rule.description || '',
            pattern: rule.pattern,
            action: rule.action,
            severity: rule.severity,
            validator: rule.validator || 'none',
            validator_weight: rule.validator_weight ?? 0.3,
            base_confidence: rule.base_confidence ?? 0.85,
            threshold: rule.threshold ?? 0.75,
            min_count: rule.min_count ?? 1,
            ignore_case: rule.ignore_case ?? true,
            sample_prefix: rule.sample_prefix ?? 4,
            sample_suffix: rule.sample_suffix ?? 4,
            tags: Array.isArray(rule.tags) ? rule.tags.join(', ') : (rule.tags || ''),
            enabled: rule.enabled ?? true,
            context: {
              terms: Array.isArray(rule.context?.terms) ? rule.context.terms.join(', ') : (rule.context?.terms || ''),
              window: rule.context?.window ?? 50,
              boost: rule.context?.boost ?? 0.15,
              required: rule.context?.required ?? false,
            },
            fixtures: {
              positive: Array.isArray(rule.fixtures?.positive) ? rule.fixtures.positive.join('\n') : '',
              negative: Array.isArray(rule.fixtures?.negative) ? rule.fixtures.negative.join('\n') : '',
            },
          });
        } catch (err) {
          setError(err.message || 'Failed to load rule');
        } finally {
          setLoading(false);
        }
      };
      loadRule();
    }
  }, [id, isEditing]);

  const runTest = async () => {
    if (!formData.pattern) return;
    setTesting(true);
    try {
      const payload = {
        ...formData,
        tags: formData.tags.split(',').map((t) => t.trim()).filter(Boolean),
        context: {
          ...formData.context,
          terms: formData.context.terms.split(',').map((t) => t.trim()).filter(Boolean),
        },
        fixtures: {
          positive: formData.fixtures.positive.split('\n').map((t) => t.trim()).filter(Boolean),
          negative: formData.fixtures.negative.split('\n').map((t) => t.trim()).filter(Boolean),
        },
      };
      const result = await api.tryRule(payload, sampleText);
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
      const payload = {
        ...formData,
        tags: formData.tags.split(',').map((t) => t.trim()).filter(Boolean),
        context: {
          ...formData.context,
          terms: formData.context.terms.split(',').map((t) => t.trim()).filter(Boolean),
        },
        fixtures: {
          positive: formData.fixtures.positive.split('\n').map((t) => t.trim()).filter(Boolean),
          negative: formData.fixtures.negative.split('\n').map((t) => t.trim()).filter(Boolean),
        },
        note: saveNote.trim(),
      };

      if (isEditing) {
        await api.updateRule(id, payload);
      } else {
        await api.createRule(payload);
      }
      navigate('/rules');
    } catch (err) {
      alert(`Save failed: ${err.message}`);
    } finally {
      setSaving(false);
    }
  };

  if (loading) {
    return <div className="py-24 text-center text-slate-400 font-mono text-sm">Loading rule editor...</div>;
  }

  return (
    <div className="space-y-6 animate-in fade-in duration-300">
      
      {/* Top Header */}
      <div className="flex items-center justify-between">
        <button
          onClick={() => navigate('/rules')}
          className="inline-flex items-center space-x-1.5 text-xs text-slate-400 hover:text-white transition-colors"
        >
          <ArrowLeft className="w-4 h-4" />
          <span>Back to Rules</span>
        </button>

        <button
          onClick={() => { setSaveNote(''); setSaveModal(true); }}
          className="px-4 py-2 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold shadow-lg shadow-indigo-600/30 flex items-center space-x-1.5 transition-all"
        >
          <Save className="w-4 h-4" />
          <span>{isEditing ? 'Save Changes' : 'Create Rule'}</span>
        </button>
      </div>

      {error && (
        <div className="p-3 rounded-lg bg-rose-500/10 border border-rose-500/30 text-rose-400 text-xs">
          {error}
        </div>
      )}

      {/* Split Screen Layout */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        
        {/* Left Panel: Rule Definition Form */}
        <div className="glass-card rounded-xl p-6 border border-slate-800 space-y-5">
          <h2 className="text-sm font-semibold text-white flex items-center space-x-2 border-b border-slate-800 pb-3">
            <SlidersHorizontal className="w-4 h-4 text-indigo-400" />
            <span>Rule Configuration</span>
          </h2>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-xs">
            <div>
              <label className="block text-slate-300 font-medium mb-1">Rule ID (Slug)</label>
              <input
                type="text"
                disabled={isEditing}
                value={formData.id}
                onChange={(e) => setFormData({ ...formData, id: e.target.value })}
                placeholder="strictly-confidential"
                className="w-full px-3 py-2 bg-surface-850 border border-slate-700 rounded-lg text-white font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500 disabled:opacity-50"
              />
            </div>

            <div>
              <label className="block text-slate-300 font-medium mb-1">Rule Name</label>
              <input
                type="text"
                value={formData.name}
                onChange={(e) => setFormData({ ...formData, name: e.target.value })}
                placeholder="Strictly Confidential Marking"
                className="w-full px-3 py-2 bg-surface-850 border border-slate-700 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
            </div>

            <div className="sm:col-span-2">
              <label className="block text-slate-300 font-medium mb-1">Description</label>
              <input
                type="text"
                value={formData.description}
                onChange={(e) => setFormData({ ...formData, description: e.target.value })}
                placeholder="Holds documents containing executive classification banners"
                className="w-full px-3 py-2 bg-surface-850 border border-slate-700 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
            </div>

            <div className="sm:col-span-2">
              <label className="block text-slate-300 font-medium mb-1">Regular Expression Pattern (RE2 Linear)</label>
              <input
                type="text"
                value={formData.pattern}
                onChange={(e) => setFormData({ ...formData, pattern: e.target.value })}
                placeholder="\bstrictly\s+confidential\b"
                className="w-full px-3 py-2 bg-surface-850 border border-slate-700 rounded-lg text-white font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
            </div>

            <div>
              <label className="block text-slate-300 font-medium mb-1">Action on Match</label>
              <select
                value={formData.action}
                onChange={(e) => setFormData({ ...formData, action: e.target.value })}
                className="w-full px-3 py-2 bg-surface-850 border border-slate-700 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
              >
                <option value="hold">hold (Stop in CUPS, SOC Review)</option>
                <option value="block">block (Destroy Spool File)</option>
                <option value="log">log (Allow & Alert SIEM)</option>
              </select>
            </div>

            <div>
              <label className="block text-slate-300 font-medium mb-1">Severity (0–10)</label>
              <input
                type="number"
                min="0"
                max="10"
                value={formData.severity}
                onChange={(e) => setFormData({ ...formData, severity: parseInt(e.target.value) || 0 })}
                className="w-full px-3 py-2 bg-surface-850 border border-slate-700 rounded-lg text-white font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
            </div>

            <div>
              <label className="block text-slate-300 font-medium mb-1">Base Confidence (0.0–1.0)</label>
              <input
                type="number"
                step="0.05"
                min="0"
                max="1"
                value={formData.base_confidence}
                onChange={(e) => setFormData({ ...formData, base_confidence: parseFloat(e.target.value) || 0 })}
                className="w-full px-3 py-2 bg-surface-850 border border-slate-700 rounded-lg text-white font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
            </div>

            <div>
              <label className="block text-slate-300 font-medium mb-1">Trigger Threshold (0.0–1.0)</label>
              <input
                type="number"
                step="0.05"
                min="0"
                max="1"
                value={formData.threshold}
                onChange={(e) => setFormData({ ...formData, threshold: parseFloat(e.target.value) || 0 })}
                className="w-full px-3 py-2 bg-surface-850 border border-slate-700 rounded-lg text-white font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
            </div>

            <div>
              <label className="block text-slate-300 font-medium mb-1">Min Matches Per Page</label>
              <input
                type="number"
                min="1"
                value={formData.min_count}
                onChange={(e) => setFormData({ ...formData, min_count: parseInt(e.target.value) || 1 })}
                className="w-full px-3 py-2 bg-surface-850 border border-slate-700 rounded-lg text-white font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
            </div>

            <div>
              <label className="block text-slate-300 font-medium mb-1">Tags (Comma-separated)</label>
              <input
                type="text"
                value={formData.tags}
                onChange={(e) => setFormData({ ...formData, tags: e.target.value })}
                placeholder="confidential, corporate"
                className="w-full px-3 py-2 bg-surface-850 border border-slate-700 rounded-lg text-white font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
            </div>

            <div className="sm:col-span-2 flex items-center space-x-2 pt-1">
              <input
                type="checkbox"
                id="ignore_case"
                checked={formData.ignore_case}
                onChange={(e) => setFormData({ ...formData, ignore_case: e.target.checked })}
                className="rounded bg-surface-850 border-slate-700 text-indigo-600 focus:ring-indigo-500 w-4 h-4"
              />
              <label htmlFor="ignore_case" className="text-slate-300 font-medium">
                Case Insensitive Matching
              </label>
            </div>
          </div>

          {/* Context Boost Section */}
          <div className="border-t border-slate-800 pt-4 space-y-3">
            <h3 className="text-xs font-semibold text-slate-300">Context Proximity Boost (Optional)</h3>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-xs">
              <div className="sm:col-span-2">
                <label className="block text-slate-400 mb-1">Nearby Terms (within 50 chars)</label>
                <input
                  type="text"
                  value={formData.context.terms}
                  onChange={(e) => setFormData({ ...formData, context: { ...formData.context, terms: e.target.value } })}
                  placeholder="internal, secret, restricted, proprietary, privileged"
                  className="w-full px-3 py-2 bg-surface-850 border border-slate-700 rounded-lg text-white font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500"
                />
              </div>

              <div>
                <label className="block text-slate-400 mb-1">Score Boost</label>
                <input
                  type="number"
                  step="0.05"
                  value={formData.context.boost}
                  onChange={(e) => setFormData({ ...formData, context: { ...formData.context, boost: parseFloat(e.target.value) || 0 } })}
                  className="w-full px-3 py-2 bg-surface-850 border border-slate-700 rounded-lg text-white font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500"
                />
              </div>
            </div>
          </div>
        </div>

        {/* Right Panel: Test Fixtures & Real-time Live Studio */}
        <div className="space-y-6">
          
          {/* Mandatory Test Fixtures */}
          <div className="glass-card rounded-xl p-6 border border-slate-800 space-y-4">
            <h2 className="text-sm font-semibold text-white flex items-center space-x-2 border-b border-slate-800 pb-3">
              <Sparkles className="w-4 h-4 text-indigo-400" />
              <span>Mandatory Test Fixtures (Gate)</span>
            </h2>
            <p className="text-[11px] text-slate-400">
              One test per line. The rule must match all positive examples and zero negative examples before saving.
            </p>

            <div className="space-y-3 text-xs">
              <div>
                <label className="block text-emerald-400 font-semibold mb-1">Positive Fixtures (Must Match)</label>
                <textarea
                  rows={3}
                  value={formData.fixtures.positive}
                  onChange={(e) => setFormData({ ...formData, fixtures: { ...formData.fixtures, positive: e.target.value } })}
                  placeholder="STRICTLY CONFIDENTIAL: This document is restricted&#10;Strictly Confidential - Q3 Financial Report"
                  className="w-full px-3 py-2 bg-surface-850 border border-slate-700 rounded-lg text-white font-mono text-xs focus:outline-none focus:ring-2 focus:ring-emerald-500"
                />
              </div>

              <div>
                <label className="block text-rose-400 font-semibold mb-1">Negative Fixtures (Must NOT Match)</label>
                <textarea
                  rows={3}
                  value={formData.fixtures.negative}
                  onChange={(e) => setFormData({ ...formData, fixtures: { ...formData.fixtures, negative: e.target.value } })}
                  placeholder="Please treat this email as confidential&#10;The conference was strictly informative"
                  className="w-full px-3 py-2 bg-surface-850 border border-slate-700 rounded-lg text-white font-mono text-xs focus:outline-none focus:ring-2 focus:ring-rose-500"
                />
              </div>
            </div>
          </div>

          {/* Live Real-Time Tester Studio */}
          <div className="glass-card rounded-xl p-6 border border-slate-800 space-y-4">
            <div className="flex items-center justify-between border-b border-slate-800 pb-3">
              <h2 className="text-sm font-semibold text-white flex items-center space-x-2">
                <Play className="w-4 h-4 text-indigo-400" />
                <span>Live Studio Tester</span>
              </h2>

              <button
                onClick={runTest}
                disabled={testing}
                className="px-3 py-1 rounded bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold shadow-md transition-all"
              >
                {testing ? 'Testing...' : 'Test Pattern'}
              </button>
            </div>

            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1">Paste Sample Document Text</label>
              <textarea
                rows={3}
                value={sampleText}
                onChange={(e) => setSampleText(e.target.value)}
                className="w-full px-3 py-2 bg-surface-850 border border-slate-700 rounded-lg text-white text-xs font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500"
              />
            </div>

            {/* Test Results Output */}
            {testResult && (
              <div className="p-4 rounded-xl bg-surface-850 border border-slate-800 space-y-3 font-mono text-xs">
                {testResult.error ? (
                  <div className="text-rose-400">{testResult.error}</div>
                ) : (
                  <>
                    <div className="flex items-center justify-between">
                      <span className="font-semibold text-slate-300">Action:</span>
                      <span className={`px-2 py-0.5 rounded font-bold uppercase ${
                        testResult.action === 'hold' ? 'bg-amber-500/10 text-amber-400 border border-amber-500/30' : 'bg-slate-800 text-slate-400'
                      }`}>
                        {testResult.action}
                      </span>
                    </div>

                    <div>
                      <span className="font-semibold text-slate-300">Fires: </span>
                      <span className={testResult.fires ? 'text-emerald-400 font-bold' : 'text-slate-500'}>
                        {testResult.fires ? 'YES (DLP Rule Triggered)' : 'NO (Allowed)'}
                      </span>
                    </div>

                    {testResult.matches?.length > 0 && (
                      <div className="space-y-1 pt-1 border-t border-slate-800">
                        <div className="text-slate-400 font-semibold">Matched Samples:</div>
                        {testResult.matches.map((m, i) => (
                          <div key={i} className="p-2 rounded bg-surface-900 border border-slate-800 flex justify-between text-[11px]">
                            <span className="text-white font-bold">{m.sample}</span>
                            <span className="text-indigo-400">Score: {m.score}</span>
                          </div>
                        ))}
                      </div>
                    )}

                    {testResult.fixture_failures?.length > 0 && (
                      <div className="p-2.5 rounded bg-rose-500/10 border border-rose-500/30 text-rose-400 text-[11px]">
                        <div className="font-bold mb-1">Fixture Failures (Must resolve before saving):</div>
                        {testResult.fixture_failures.map((f, i) => (
                          <div key={i}>&bull; {f.kind}: {f.text}</div>
                        ))}
                      </div>
                    )}
                  </>
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
        title={isEditing ? 'Save Rule Modifications' : 'Create New DLP Rule'}
      >
        <div className="space-y-4">
          <p className="text-xs text-slate-300">
            Rule updates take effect across all print queues in real time. Please provide a permanent audit justification.
          </p>

          <div>
            <label className="block text-xs font-medium text-slate-400 mb-1">Reason for this change</label>
            <input
              type="text"
              value={saveNote}
              onChange={(e) => setSaveNote(e.target.value)}
              placeholder="e.g., Added strictly-confidential classification rule for corporate compliance"
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
