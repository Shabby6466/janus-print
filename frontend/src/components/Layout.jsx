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
          <div className="flex items-center justify-between h-20">
            
            {/* Logo & Brand */}
            <div className="flex items-center space-x-8">
              <NavLink to="/" className="flex items-center space-x-3 group">
                <div className="relative flex items-center justify-center w-10 h-10 rounded-xl bg-indigo-600/20 border border-indigo-500/40 group-hover:border-indigo-400 transition-colors">
                  <Printer className="w-5 h-5 text-indigo-400" />
                  <span className="absolute -top-1 -right-1 w-3 h-3 bg-indigo-500 rounded-full radar-live" />
                </div>
                <div className="flex items-baseline space-x-2">
                  <span className="text-xl font-bold tracking-tight text-white">janus-print</span>
                  <span className="text-xs font-bold uppercase px-2 py-0.5 rounded bg-indigo-500/10 text-indigo-400 border border-indigo-500/30">
                    DLP
                  </span>
                </div>
              </NavLink>

              {/* Navigation Links */}
              <nav className="hidden md:flex items-center space-x-1.5">
                {navItems.map((item) => {
                  const Icon = item.icon;
                  const isActive = location.pathname === item.to || (item.to !== '/' && location.pathname.startsWith(item.to));
                  return (
                    <NavLink
                      key={item.to}
                      to={item.to}
                      className={`flex items-center space-x-2 px-3.5 py-2 rounded-xl text-sm font-medium transition-all ${
                        isActive
                          ? 'bg-indigo-600/20 text-indigo-300 border border-indigo-500/40 font-semibold shadow-sm'
                          : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/70'
                      }`}
                    >
                      <Icon className="w-4 h-4" />
                      <span>{item.label}</span>
                    </NavLink>
                  );
                })}
              </nav>
            </div>

            {/* Right Controls & User Menu */}
            <div className="flex items-center space-x-5">
              
              {/* Auto-Sync Toggle */}
              <button
                onClick={() => setAutoSync(!autoSync)}
                title={autoSync ? 'Live auto-sync active (4s)' : 'Auto-sync paused'}
                className={`flex items-center space-x-2 px-3.5 py-1.5 rounded-lg text-xs font-mono font-semibold border transition-colors ${
                  autoSync
                    ? 'bg-emerald-950/40 text-emerald-400 border-emerald-800/80 shadow-sm'
                    : 'bg-slate-800 text-slate-400 border-slate-700'
                }`}
              >
                <RefreshCw className={`w-3.5 h-3.5 ${syncPulse ? 'rotate-180 transition-transform duration-700' : ''}`} />
                <span className="hidden sm:inline">{autoSync ? 'LIVE' : 'PAUSED'}</span>
              </button>

              {/* User Info & Role */}
              {user && (
                <div className="flex items-center space-x-4 pl-4 border-l border-slate-800">
                  <div className="text-right hidden sm:block">
                    <div className="text-sm font-semibold text-slate-200">{user.display_name || user.username}</div>
                    <div className="text-xs uppercase font-mono text-indigo-400 font-medium">{user.role}</div>
                  </div>

                  <button
                    onClick={logout}
                    title="Sign out"
                    className="p-2 text-slate-400 hover:text-rose-400 hover:bg-rose-500/10 rounded-xl border border-transparent hover:border-rose-500/20 transition-colors"
                  >
                    <LogOut className="w-5 h-5" />
                  </button>
                </div>
              )}

            </div>
          </div>
        </div>
      </header>

      {/* Main Content Area */}
      <main className="flex-1 max-w-[92rem] w-full mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <Outlet context={{ autoSync }} />
      </main>

      {/* Footer */}
      <footer className="border-t border-slate-800/60 py-5 bg-surface-950/60 text-center text-xs text-slate-400">
        janus-print DLP &middot; Zero-ReDoS Linear Time Inspection &middot; Spooler Level Hold & Block
      </footer>
    </div>
  );
}
