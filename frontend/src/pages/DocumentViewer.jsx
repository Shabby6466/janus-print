import React, { useState, useEffect } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import { 
  ArrowLeft, 
  ChevronLeft, 
  ChevronRight, 
  ZoomIn, 
  ZoomOut, 
  RotateCw, 
  Download, 
  ShieldAlert, 
  Lock 
} from 'lucide-react';
import { api } from '../api';

export function DocumentViewer() {
  const { id } = useParams();
  const navigate = useNavigate();

  const [job, setJob] = useState(null);
  const [currentPage, setCurrentPage] = useState(1);
  const [zoom, setZoom] = useState(100);
  const [rotation, setRotation] = useState(0);
  const [loading, setLoading] = useState(true);
  const [imgError, setImgError] = useState(false);

  useEffect(() => {
    const fetchJob = async () => {
      try {
        const data = await api.getJob(id);
        setJob(data);
      } catch (err) {
        console.error('Failed to load job for viewer', err);
      } finally {
        setLoading(false);
      }
    };
    fetchJob();
  }, [id]);

  const totalPages = job?.page_count || 1;
  const previewUrl = `/api/v1/jobs/${id}/pages/${currentPage}/preview`;

  return (
    <div className="space-y-4 animate-in fade-in duration-300">
      
      {/* Top Controls Bar */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 p-3 rounded-xl bg-surface-850 border border-slate-800">
        <div className="flex items-center space-x-3">
          <button
            onClick={() => navigate(`/jobs/${id}`)}
            className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-slate-700 transition-colors"
          >
            <ArrowLeft className="w-4 h-4" />
          </button>
          <div>
            <h2 className="text-sm font-semibold text-white truncate max-w-sm">
              {job?.title || `Job #${id}`}
            </h2>
            <div className="text-[10px] text-slate-400 font-mono">
              Watermarked SOC Triage Preview &middot; Page {currentPage} of {totalPages}
            </div>
          </div>
        </div>

        {/* Zoom & Page Navigation */}
        <div className="flex items-center space-x-2">
          
          {/* Zoom controls */}
          <div className="flex items-center space-x-1 bg-surface-900 border border-slate-700/60 rounded-lg p-1 text-xs">
            <button
              onClick={() => setZoom(Math.max(50, zoom - 20))}
              className="p-1 text-slate-400 hover:text-white rounded hover:bg-slate-800 transition-colors"
              title="Zoom out"
            >
              <ZoomOut className="w-3.5 h-3.5" />
            </button>
            <span className="px-1.5 font-mono text-[11px] text-slate-300 min-w-[3rem] text-center">{zoom}%</span>
            <button
              onClick={() => setZoom(Math.min(250, zoom + 20))}
              className="p-1 text-slate-400 hover:text-white rounded hover:bg-slate-800 transition-colors"
              title="Zoom in"
            >
              <ZoomIn className="w-3.5 h-3.5" />
            </button>
            <button
              onClick={() => setRotation((rotation + 90) % 360)}
              className="p-1 text-slate-400 hover:text-white rounded hover:bg-slate-800 transition-colors"
              title="Rotate 90deg"
            >
              <RotateCw className="w-3.5 h-3.5" />
            </button>
          </div>

          {/* Page switch buttons */}
          <div className="flex items-center space-x-1 bg-surface-900 border border-slate-700/60 rounded-lg p-1 text-xs">
            <button
              disabled={currentPage <= 1}
              onClick={() => { setCurrentPage(currentPage - 1); setImgError(false); }}
              className="p-1 text-slate-400 hover:text-white rounded hover:bg-slate-800 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              <ChevronLeft className="w-4 h-4" />
            </button>
            <span className="px-2 font-mono text-xs text-slate-200">
              {currentPage} / {totalPages}
            </span>
            <button
              disabled={currentPage >= totalPages}
              onClick={() => { setCurrentPage(currentPage + 1); setImgError(false); }}
              className="p-1 text-slate-400 hover:text-white rounded hover:bg-slate-800 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
            >
              <ChevronRight className="w-4 h-4" />
            </button>
          </div>

          {/* Direct Download Button (Unlocked if grant exists) */}
          <a
            href={`/api/v1/jobs/${id}/download`}
            className="px-3 py-1.5 bg-surface-900 hover:bg-slate-800 text-slate-200 border border-slate-700/60 rounded-lg text-xs font-semibold flex items-center space-x-1.5 transition-colors"
            title="Download original document (Requires approval grant)"
          >
            <Download className="w-3.5 h-3.5 text-indigo-400" />
            <span className="hidden sm:inline">Raw File</span>
          </a>
        </div>
      </div>

      {/* Main Canvas Viewport */}
      <div className="glass-card rounded-2xl border border-slate-800 p-6 min-h-[600px] flex items-center justify-center overflow-auto relative">
        {imgError ? (
          <div className="text-center p-8 max-w-md">
            <Lock className="w-10 h-10 text-amber-400/60 mx-auto mb-3" />
            <h4 className="text-sm font-semibold text-white">Preview Restricted</h4>
            <p className="text-xs text-slate-400 mt-1">
              Historical documents released or blocked in the archive require an active dual-approval access grant to generate previews.
            </p>
            <button
              onClick={() => navigate(`/jobs/${id}`)}
              className="mt-4 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-semibold"
            >
              Request Access in Job Details
            </button>
          </div>
        ) : (
          <div
            className="transition-transform duration-200 ease-out origin-center"
            style={{
              transform: `scale(${zoom / 100}) rotate(${rotation}deg)`,
            }}
          >
            <img
              src={previewUrl}
              alt={`Page ${currentPage}`}
              onError={() => setImgError(true)}
              className="rounded shadow-2xl max-w-full max-h-[750px] object-contain border border-slate-700 bg-white"
            />
          </div>
        )}
      </div>

      {/* Page Thumbnails Bar */}
      {totalPages > 1 && (
        <div className="flex items-center space-x-3 overflow-x-auto p-3 rounded-xl bg-surface-850 border border-slate-800">
          {Array.from({ length: totalPages }, (_, i) => i + 1).map((pageNum) => (
            <button
              key={pageNum}
              onClick={() => { setCurrentPage(pageNum); setImgError(false); }}
              className={`flex-shrink-0 px-3 py-2 rounded-lg border text-xs font-mono transition-all ${
                currentPage === pageNum
                  ? 'bg-indigo-600 text-white border-indigo-400 font-bold shadow-lg shadow-indigo-600/30'
                  : 'bg-surface-900 text-slate-400 border-slate-800 hover:border-slate-700 hover:text-white'
              }`}
            >
              Page {pageNum}
            </button>
          ))}
        </div>
      )}

    </div>
  );
}
