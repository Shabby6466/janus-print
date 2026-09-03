import React from 'react';
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider, useAuth } from './context/AuthContext';
import { Layout } from './components/Layout';
import { Login } from './pages/Login';
import { Dashboard } from './pages/Dashboard';
import { Queue } from './pages/Queue';
import { JobDetail } from './pages/JobDetail';
import { DocumentViewer } from './pages/DocumentViewer';
import { Rules } from './pages/Rules';
import { RuleEditor } from './pages/RuleEditor';
import { Validators } from './pages/Validators';
import { ValidatorEditor } from './pages/ValidatorEditor';
import { Printers } from './pages/Printers';
import { Documents } from './pages/Documents';
import { Audit } from './pages/Audit';
import { Users } from './pages/Users';

function ProtectedRoute({ children, requiredRole }) {
  const { user, loading, hasRole } = useAuth();

  if (loading) {
    return (
      <div className="min-h-screen bg-surface-900 flex items-center justify-center font-mono text-xs text-slate-400">
        Authenticating...
      </div>
    );
  }

  if (!user) {
    return <Navigate to="/login" replace />;
  }

  if (requiredRole && !hasRole(requiredRole)) {
    return <Navigate to="/" replace />;
  }

  return children;
}

export function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<Login />} />
          
          <Route
            element={
              <ProtectedRoute>
                <Layout />
              </ProtectedRoute>
            }
          >
            <Route path="/" element={<Dashboard />} />
            <Route path="/queue" element={<Queue />} />
            <Route path="/jobs/:id" element={<JobDetail />} />
            <Route path="/jobs/:id/view" element={<DocumentViewer />} />
            <Route path="/rules" element={<Rules />} />
            <Route path="/rules/new" element={<ProtectedRoute requiredRole="admin"><RuleEditor /></ProtectedRoute>} />
            <Route path="/rules/:id/edit" element={<ProtectedRoute requiredRole="admin"><RuleEditor /></ProtectedRoute>} />
            <Route path="/validators" element={<Validators />} />
            <Route path="/validators/new" element={<ProtectedRoute requiredRole="admin"><ValidatorEditor /></ProtectedRoute>} />
            <Route path="/validators/:id/edit" element={<ProtectedRoute requiredRole="admin"><ValidatorEditor /></ProtectedRoute>} />
            <Route path="/printers" element={<Printers />} />
            <Route path="/documents" element={<Documents />} />
            <Route path="/audit" element={<Audit />} />
            <Route path="/users" element={<ProtectedRoute requiredRole="admin"><Users /></ProtectedRoute>} />
          </Route>

          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  );
}
