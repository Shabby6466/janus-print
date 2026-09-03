import React, { useState, useEffect } from 'react';
import { NavLink, Outlet, useLocation } from 'react-router-dom';
import { 
  ShieldAlert, 
  Printer, 
  SlidersHorizontal, 
  Binary, 
  FileText, 
  History, 
  Users as UsersIcon, 
  LayoutDashboard, 
  LogOut, 
  Radio, 
  RefreshCw,
  Sun,
  Moon
} from 'lucide-react';
import { useAuth } from '../context/AuthContext';

export function Layout() {
  const { user, logout, hasRole } = useAuth();
  const location = useLocation();
  const [autoSync, setAutoSync] = useState(true);
  const [syncPulse, setSyncPulse] = useState(false);

  // Trigger pulse effect every 4 seconds when auto-sync is active
  useEffect(() => {
    if (!autoSync) return;
    const interval = setInterval(() => {
      setSyncPulse(true);
      setTimeout(() => setSyncPulse(false), 800);
    }, 4000);
    return () => clearInterval(interval);
  }, [autoSync]);

  const navItems = [
    { to: '/', label: 'Dashboard', icon: LayoutDashboard },
    { to: '/queue', label: 'Queue', icon: ShieldAlert },
    { to: '/rules', label: 'Rules', icon: SlidersHorizontal },
    { to: '/validators', label: 'Validators', icon: Binary },
    { to: '/documents', label: 'Documents', icon: FileText },
    { to: '/printers', label: 'Printers', icon: Printer },
    { to: '/audit', label: 'Audit', icon: History },
    ...(hasRole('admin') ? [{ to: '/users', label: 'Users', icon: UsersIcon }] : []),
  ];

  return (
    <div className="min-h-screen bg-surface-900 flex flex-col">
      {/* Top Navbar */}
      <header className="sticky top-0 z-40 glass border-b border-slate-800/80">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between h-16">
            
            {/* Logo & Brand */}
            <div className="flex items-center space-x-6">
              <NavLink to="/" className="flex items-center space-x-2.5 group">
                <div className="relative flex items-center justify-center w-8 h-8 rounded-lg bg-indigo-600/20 border border-indigo-500/40 group-hover:border-indigo-400 transition-colors">
                  <Printer className="w-4 h-4 text-indigo-400" />
                  <span className="absolute -top-1 -right-1 w-2.5 h-2.5 bg-indigo-500 rounded-full radar-live" />
                </div>
                <div className="flex items-baseline space-x-1.5">
                  <span className="text-lg font-bold tracking-tight text-white">janus-print</span>
                  <span className="text-[10px] font-semibold uppercase px-1.5 py-0.5 rounded bg-indigo-500/10 text-indigo-400 border border-indigo-500/30">
                    DLP
                  </span>
                </div>
              </NavLink>

              {/* Navigation Links */}
              <nav className="hidden md:flex items-center space-x-1">
                {navItems.map((item) => {
                  const Icon = item.icon;
                  const isActive = location.pathname === item.to || (item.to !== '/' && location.pathname.startsWith(item.to));
                  return (
                    <NavLink
                      key={item.to}
                      to={item.to}
                      className={`flex items-center space-x-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
                        isActive
                          ? 'bg-indigo-600/15 text-indigo-300 border border-indigo-500/30 font-semibold'
                          : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/60'
                      }`}
                    >
                      <Icon className="w-3.5 h-3.5" />
                      <span>{item.label}</span>
                    </NavLink>
                  );
                })}
              </nav>
            </div>

            {/* Right Controls & User Menu */}
            <div className="flex items-center space-x-4">
              
              {/* Auto-Sync Toggle */}
              <button
                onClick={() => setAutoSync(!autoSync)}
                title={autoSync ? 'Live auto-sync active (4s)' : 'Auto-sync paused'}
                className={`flex items-center space-x-1.5 px-2.5 py-1 rounded-md text-xs font-mono border transition-colors ${
                  autoSync
                    ? 'bg-emerald-950/40 text-emerald-400 border-emerald-800/60'
                    : 'bg-slate-800 text-slate-400 border-slate-700'
                }`}
              >
                <RefreshCw className={`w-3 h-3 ${syncPulse ? 'rotate-180 transition-transform duration-700' : ''}`} />
                <span className="hidden sm:inline">{autoSync ? 'LIVE' : 'PAUSED'}</span>
              </button>

              {/* User Info & Role */}
              {user && (
                <div className="flex items-center space-x-3 pl-3 border-l border-slate-800">
                  <div className="text-right hidden sm:block">
                    <div className="text-xs font-medium text-slate-200">{user.display_name || user.username}</div>
                    <div className="text-[10px] uppercase font-mono text-slate-400">{user.role}</div>
                  </div>

                  <button
                    onClick={logout}
                    title="Sign out"
                    className="p-1.5 text-slate-400 hover:text-rose-400 hover:bg-rose-500/10 rounded-lg border border-transparent hover:border-rose-500/20 transition-colors"
                  >
                    <LogOut className="w-4 h-4" />
                  </button>
                </div>
              )}

            </div>
          </div>
        </div>
      </header>

      {/* Main Content Area */}
      <main className="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <Outlet context={{ autoSync }} />
      </main>

      {/* Footer */}
      <footer className="border-t border-slate-800/60 py-4 bg-surface-950/60 text-center text-xs text-slate-400">
        janus-print DLP &middot; Zero-ReDoS Linear Time Inspection &middot; Spooler Level Hold & Block
      </footer>
    </div>
  );
}
