import React, { useState, useEffect } from 'react';
import { FileText, Upload, Trash2, FileCheck, AlertCircle } from 'lucide-react';
import { api } from '../api';
import { useAuth } from '../context/AuthContext';

export function Documents() {
  const { hasRole } = useAuth();
  const [documents, setDocuments] = useState([]);
  const [loading, setLoading] = useState(true);

  // Upload state
  const [file, setFile] = useState(null);
  const [docName, setDocName] = useState('');
  const [action, setAction] = useState('hold');
  const [severity, setSeverity] = useState(8);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState('');

  const fetchDocs = async () => {
    try {
      const data = await api.getDocuments();
      setDocuments(data);
    } catch (err) {
      console.error('Failed to load documents', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchDocs();
  }, []);

  const handleUpload = async (e) => {
    e.preventDefault();
    if (!file) {
      setUploadError('Please select a file (.pdf, .txt, .docx, .ps)');
      return;
    }
    setUploadError('');
    setUploading(true);
    try {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('name', docName || file.name);
      formData.append('action', action);
      formData.append('severity', severity);

      await api.registerDocument(formData);
      setFile(null);
      setDocName('');
      fetchDocs();
    } catch (err) {
      setUploadError(err.message || 'Upload failed');
    } finally {
      setUploading(false);
    }
  };

  const handleDelete = async (doc) => {
    if (!confirm(`Delete fingerprint registration for "${doc.name}"?`)) return;
    try {
      await api.deleteDocument(doc.id);
      fetchDocs();
    } catch (err) {
      alert(`Delete failed: ${err.message}`);
    }
  };

  return (
    <div className="space-y-6 animate-in fade-in duration-300">
      
      {/* Header */}
      <div>
        <h1 className="text-xl font-bold text-white tracking-tight">Proprietary Document Fingerprints</h1>
        <p className="text-xs text-slate-400 mt-0.5">
          Exact & partial excerpt detection using winnowed 5-gram shingle indexing
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        
        {/* Upload & Register Form */}
        {hasRole('admin') && (
          <div className="glass-card rounded-xl p-5 border border-slate-800 space-y-4">
            <h2 className="text-sm font-semibold text-white flex items-center space-x-2 border-b border-slate-800 pb-3">
              <Upload className="w-4 h-4 text-indigo-400" />
              <span>Register New Document</span>
            </h2>

            {uploadError && (
              <div className="p-3 rounded-lg bg-rose-500/10 border border-rose-500/30 text-rose-400 text-xs flex items-center space-x-2">
                <AlertCircle className="w-4 h-4 shrink-0" />
                <span>{uploadError}</span>
              </div>
            )}

            <form onSubmit={handleUpload} className="space-y-3 text-xs">
              <div>
                <label className="block text-slate-300 font-medium mb-1">Select Document File</label>
                <input
                  type="file"
                  onChange={(e) => {
                    const selected = e.target.files[0];
                    setFile(selected);
                    if (selected && !docName) setDocName(selected.name);
                  }}
                  className="w-full text-slate-400 file:mr-3 file:py-1.5 file:px-3 file:rounded-lg file:border-0 file:text-xs file:font-semibold file:bg-indigo-600 file:text-white hover:file:bg-indigo-500 cursor-pointer"
                />
              </div>

              <div>
                <label className="block text-slate-300 font-medium mb-1">Document Label / Title</label>
                <input
                  type="text"
                  value={docName}
                  onChange={(e) => setDocName(e.target.value)}
                  placeholder="e.g., M&A Agreement 2026"
                  className="w-full px-3 py-2 bg-surface-850 border border-slate-700 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-slate-300 font-medium mb-1">Action</label>
                  <select
                    value={action}
                    onChange={(e) => setAction(e.target.value)}
                    className="w-full px-3 py-2 bg-surface-850 border border-slate-700 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  >
                    <option value="hold">hold</option>
                    <option value="block">block</option>
                    <option value="log">log</option>
                  </select>
                </div>

                <div>
                  <label className="block text-slate-300 font-medium mb-1">Severity</label>
                  <input
                    type="number"
                    min="1"
                    max="10"
                    value={severity}
                    onChange={(e) => setSeverity(parseInt(e.target.value) || 8)}
                    className="w-full px-3 py-2 bg-surface-850 border border-slate-700 rounded-lg text-white font-mono focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  />
                </div>
              </div>

              <button
                type="submit"
                disabled={uploading || !file}
                className="w-full py-2 bg-indigo-600 hover:bg-indigo-500 text-white font-semibold rounded-lg shadow-lg shadow-indigo-600/30 transition-all disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {uploading ? 'Fingerprinting...' : 'Generate Fingerprint & Register'}
              </button>
            </form>
          </div>
        )}

        {/* Registered Corpus List */}
        <div className={`space-y-4 ${hasRole('admin') ? 'lg:col-span-2' : 'lg:col-span-3'}`}>
          <div className="glass-card rounded-xl border border-slate-800 overflow-hidden">
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead className="bg-surface-850 text-slate-400 uppercase tracking-wider font-semibold border-b border-slate-800">
                  <tr>
                    <th className="px-4 py-3">Document Name</th>
                    <th className="px-4 py-3">Shingles</th>
                    <th className="px-4 py-3">Action</th>
                    <th className="px-4 py-3">Severity</th>
                    <th className="px-4 py-3">Registered At</th>
                    {hasRole('admin') && <th className="px-4 py-3 text-right">Action</th>}
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/60">
                  {documents.length > 0 ? (
                    documents.map((doc) => (
                      <tr key={doc.id} className="hover:bg-slate-800/30 transition-colors">
                        <td className="px-4 py-3">
                          <div className="font-semibold text-white flex items-center space-x-1.5">
                            <FileCheck className="w-4 h-4 text-indigo-400 shrink-0" />
                            <span>{doc.name}</span>
                          </div>
                          <div className="text-[10px] text-slate-400 font-mono mt-0.5 truncate max-w-xs" title={doc.exact_sha256}>
                            {doc.exact_sha256}
                          </div>
                        </td>
                        <td className="px-4 py-3 font-mono text-indigo-300 font-bold">{doc.shingle_count}</td>
                        <td className="px-4 py-3 font-mono uppercase font-semibold text-amber-400">{doc.action}</td>
                        <td className="px-4 py-3 font-mono font-bold text-slate-200">{doc.severity}</td>
                        <td className="px-4 py-3 font-mono text-slate-400">
                          {doc.created_at ? new Date(doc.created_at).toLocaleDateString() : '-'}
                        </td>
                        {hasRole('admin') && (
                          <td className="px-4 py-3 text-right">
                            <button
                              onClick={() => handleDelete(doc)}
                              className="p-1 text-slate-400 hover:text-rose-400 transition-colors"
                              title="Delete registration"
                            >
                              <Trash2 className="w-3.5 h-3.5" />
                            </button>
                          </td>
                        )}
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td colSpan="6" className="px-4 py-8 text-center text-slate-500">
                        {loading ? 'Loading corpus...' : 'No documents registered in fingerprint corpus.'}
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>

      </div>

    </div>
  );
}
