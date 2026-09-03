import React, { useState, useEffect } from 'react';
import { Users as UsersIcon, UserPlus, Key, Shield, UserCheck } from 'lucide-react';
import { api } from '../api';
import { Modal } from '../components/Modal';

const ROLES = ['viewer', 'analyst', 'approver', 'admin'];

export function Users() {
  const [users, setUsers] = useState([]);
  const [loading, setLoading] = useState(true);

  // Add User Modal
  const [addModal, setAddModal] = useState(false);
  const [newUsername, setNewUsername] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [newRole, setNewRole] = useState('analyst');
  const [actionLoading, setActionLoading] = useState(false);

  // Edit User Modal
  const [editModal, setEditModal] = useState(false);
  const [selectedUser, setSelectedUser] = useState(null);
  const [editRole, setEditRole] = useState('analyst');
  const [editPassword, setEditPassword] = useState('');

  const fetchUsers = async () => {
    try {
      const data = await api.getUsers();
      setUsers(data);
    } catch (err) {
      console.error('Failed to load users', err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchUsers();
  }, []);

  const handleAddUser = async (e) => {
    e.preventDefault();
    if (!newUsername || !newPassword) {
      alert('Username and password are required');
      return;
    }
    setActionLoading(true);
    try {
      await api.createUser({
        username: newUsername.trim(),
        password: newPassword,
        role: newRole,
      });
      setAddModal(false);
      setNewUsername('');
      setNewPassword('');
      fetchUsers();
    } catch (err) {
      alert(`Failed to create user: ${err.message}`);
    } finally {
      setActionLoading(false);
    }
  };

  const handleUpdateUser = async (e) => {
    e.preventDefault();
    setActionLoading(true);
    try {
      const payload = { role: editRole };
      if (editPassword.trim()) {
        payload.password = editPassword.trim();
      }
      await api.updateUser(selectedUser.id, payload);
      setEditModal(false);
      setSelectedUser(null);
      setEditPassword('');
      fetchUsers();
    } catch (err) {
      alert(`Update failed: ${err.message}`);
    } finally {
      setActionLoading(false);
    }
  };

  return (
    <div className="space-y-6 animate-in fade-in duration-300">
      
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-white tracking-tight">Console User Accounts & Roles</h1>
          <p className="text-xs text-slate-400 mt-0.5">
            Role hierarchy: Admin &gt; Approver &gt; Analyst &gt; Viewer
          </p>
        </div>

        <button
          onClick={() => setAddModal(true)}
          className="px-3.5 py-1.5 rounded-lg bg-indigo-600 hover:bg-indigo-500 text-white text-xs font-semibold shadow-lg shadow-indigo-600/30 flex items-center space-x-1.5 transition-all"
        >
          <UserPlus className="w-3.5 h-3.5" />
          <span>Add Operator</span>
        </button>
      </div>

      {/* Users Table */}
      <div className="glass-card rounded-xl border border-slate-800 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="bg-surface-850 text-slate-400 uppercase tracking-wider font-semibold border-b border-slate-800">
              <tr>
                <th className="px-4 py-3">Username</th>
                <th className="px-4 py-3">Role</th>
                <th className="px-4 py-3">Status</th>
                <th className="px-4 py-3">Created</th>
                <th className="px-4 py-3 text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60">
              {users.map((u) => (
                <tr key={u.id} className="hover:bg-slate-800/30 transition-colors">
                  <td className="px-4 py-3 font-semibold text-white">{u.username}</td>
                  <td className="px-4 py-3">
                    <span className="px-2 py-0.5 rounded text-[10px] font-mono font-semibold bg-indigo-500/10 text-indigo-300 border border-indigo-500/30 uppercase">
                      {u.role}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <span className="inline-flex items-center text-emerald-400 text-xs">
                      <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 mr-1.5" />
                      Active
                    </span>
                  </td>
                  <td className="px-4 py-3 text-slate-400 font-mono">
                    {u.created_at ? new Date(u.created_at).toLocaleDateString() : '-'}
                  </td>
                  <td className="px-4 py-3 text-right">
                    <button
                      onClick={() => {
                        setSelectedUser(u);
                        setEditRole(u.role);
                        setEditPassword('');
                        setEditModal(true);
                      }}
                      className="px-2.5 py-1 bg-surface-850 hover:bg-slate-700 text-slate-300 border border-slate-700 rounded text-xs font-medium transition-colors"
                    >
                      Edit Account
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Add User Modal */}
      <Modal
        isOpen={addModal}
        onClose={() => setAddModal(false)}
        title="Create Console Operator"
      >
        <form onSubmit={handleAddUser} className="space-y-4 text-xs">
          <div>
            <label className="block text-slate-300 font-medium mb-1">Username</label>
            <input
              type="text"
              value={newUsername}
              onChange={(e) => setNewUsername(e.target.value)}
              placeholder="analyst_jane"
              className="w-full px-3 py-2 bg-surface-850 border border-slate-700 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
              required
            />
          </div>

          <div>
            <label className="block text-slate-300 font-medium mb-1">Temporary Password</label>
            <input
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              placeholder="••••••••"
              className="w-full px-3 py-2 bg-surface-850 border border-slate-700 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
              required
            />
          </div>

          <div>
            <label className="block text-slate-300 font-medium mb-1">Role Assignment</label>
            <select
              value={newRole}
              onChange={(e) => setNewRole(e.target.value)}
              className="w-full px-3 py-2 bg-surface-850 border border-slate-700 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-indigo-500 font-mono"
            >
              <option value="viewer">viewer (Read-only queue & rules)</option>
              <option value="analyst">analyst (Release/Deny jobs, request content)</option>
              <option value="approver">approver (Dual approval for document contents)</option>
              <option value="admin">admin (Full CRUD, user & rule management)</option>
            </select>
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
              {actionLoading ? 'Creating...' : 'Create Account'}
            </button>
          </div>
        </form>
      </Modal>

      {/* Edit User Modal */}
      <Modal
        isOpen={editModal}
        onClose={() => setEditModal(false)}
        title={`Edit Operator: ${selectedUser?.username}`}
      >
        <form onSubmit={handleUpdateUser} className="space-y-4 text-xs">
          <div>
            <label className="block text-slate-300 font-medium mb-1">Role</label>
            <select
              value={editRole}
              onChange={(e) => setEditRole(e.target.value)}
              className="w-full px-3 py-2 bg-surface-850 border border-slate-700 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-indigo-500 font-mono"
            >
              <option value="viewer">viewer</option>
              <option value="analyst">analyst</option>
              <option value="approver">approver</option>
              <option value="admin">admin</option>
            </select>
          </div>

          <div>
            <label className="block text-slate-300 font-medium mb-1">Reset Password (Leave blank to keep unchanged)</label>
            <input
              type="password"
              value={editPassword}
              onChange={(e) => setEditPassword(e.target.value)}
              placeholder="New password..."
              className="w-full px-3 py-2 bg-surface-850 border border-slate-700 rounded-lg text-white focus:outline-none focus:ring-2 focus:ring-indigo-500"
            />
          </div>

          <div className="flex justify-end space-x-2 pt-2">
            <button
              type="button"
              onClick={() => setEditModal(false)}
              className="px-4 py-2 rounded-lg font-medium text-slate-400 hover:text-white hover:bg-slate-800 transition-colors"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={actionLoading}
              className="px-4 py-2 rounded-lg font-semibold text-white bg-indigo-600 hover:bg-indigo-500 shadow-lg shadow-indigo-600/30 transition-all"
            >
              {actionLoading ? 'Saving...' : 'Save Changes'}
            </button>
          </div>
        </form>
      </Modal>

    </div>
  );
}
