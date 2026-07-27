import React, { useEffect, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { Home, Menu, X, LogOut, Upload } from "lucide-react";
import { signOutCurrentUser } from "../features/auth/authService";

interface SidebarLayoutProps {
  children: React.ReactNode;
}

const SidebarLayout: React.FC<SidebarLayoutProps> = ({ children }) => {
  const location = useLocation();
  const navigate = useNavigate();
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  useEffect(() => {
    setMobileNavOpen(false);
  }, [location.pathname]);

  const isActive = (path: string) => {
    if (path === "/analise-orcamento" || path === "/orcamento") {
      return (
        location.pathname === "/analise-orcamento" ||
        location.pathname === "/orcamento" ||
        location.pathname.startsWith("/validacao") ||
        location.pathname.startsWith("/curva-abc")
      );
    }
    return (
      location.pathname === path || location.pathname.startsWith(`${path}/`)
    );
  };

  const menuItems = [
    { path: "/", label: "Dashboard", icon: Home },
    { path: "/analise-orcamento", label: "Análise de Orçamento", icon: Upload },
  ];

  const asideWidth = sidebarOpen ? "w-64" : "w-20";

  return (
    <div className="flex h-dvh max-h-dvh w-full overflow-hidden bg-slate-50">
      {mobileNavOpen && (
        <button
          type="button"
          className="fixed inset-0 z-40 bg-thora-ink/50 backdrop-blur-[2px] lg:hidden"
          aria-label="Fechar menu"
          onClick={() => setMobileNavOpen(false)}
        />
      )}

      <aside
        className={`fixed inset-y-0 left-0 z-50 flex flex-col border-r border-white/5 bg-linear-to-b from-thora-ink via-[#102032] to-[#0d1a27] text-white shadow-xl transition-transform duration-300 lg:static lg:translate-x-0 ${asideWidth} ${
          mobileNavOpen ? "translate-x-0" : "-translate-x-full lg:translate-x-0"
        }`}
      >
        <div
          className={`flex items-center justify-between border-b border-white/10 px-5 py-5 ${
            !sidebarOpen && "px-2"
          }`}
        >
          {sidebarOpen && (
            <Link
              to="/"
              className="group flex min-w-0 items-center gap-3"
              onClick={() => setMobileNavOpen(false)}
            >
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-linear-to-br from-thora-sky to-thora-steel font-display text-lg font-bold shadow-md shadow-thora-steel/30">
                T
              </div>
              <div className="min-w-0">
                <span className="block font-display text-xl font-bold leading-none tracking-tight">
                  Thora
                </span>
                <span className="mt-1 block truncate text-[11px] font-medium uppercase tracking-[0.14em] text-slate-400">
                  Orçamentos
                </span>
              </div>
            </Link>
          )}
          {!sidebarOpen && (
            <Link
              to="/"
              className="mx-auto flex h-10 w-10 items-center justify-center rounded-xl bg-linear-to-br from-thora-sky to-thora-steel font-display text-lg font-bold"
              aria-label="Thora"
            >
              T
            </Link>
          )}
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => setSidebarOpen(!sidebarOpen)}
              className="hidden rounded-lg p-2 text-slate-300 transition hover:bg-white/10 hover:text-white lg:inline-flex"
              aria-label={
                sidebarOpen ? "Recolher menu lateral" : "Expandir menu lateral"
              }
            >
              {sidebarOpen ? (
                <X className="h-5 w-5" />
              ) : (
                <Menu className="h-5 w-5" />
              )}
            </button>
            <button
              type="button"
              className="rounded-lg p-2 text-slate-300 transition hover:bg-white/10 hover:text-white lg:hidden"
              aria-label="Fechar menu"
              onClick={() => setMobileNavOpen(false)}
            >
              <X className="h-5 w-5" />
            </button>
          </div>
        </div>

        <nav className="flex-1 space-y-1.5 overflow-y-auto px-3 py-5">
          {menuItems.map((item) => {
            const Icon = item.icon;
            const active = isActive(item.path);
            return (
              <Link
                key={item.path}
                to={item.path}
                onClick={() => setMobileNavOpen(false)}
                className={`flex items-center gap-3 rounded-xl px-3.5 py-3 transition ${
                  active
                    ? "bg-thora-steel font-semibold text-white shadow-sm shadow-thora-steel/40"
                    : "text-slate-300 hover:bg-white/8 hover:text-white"
                }`}
                title={!sidebarOpen ? item.label : undefined}
              >
                <Icon className="h-5 w-5 shrink-0" />
                {sidebarOpen && <span className="text-sm">{item.label}</span>}
              </Link>
            );
          })}
        </nav>

        <div className="border-t border-white/10 px-3 py-4">
          <button
            type="button"
            onClick={async () => {
              await signOutCurrentUser();
              navigate("/login", { replace: true });
            }}
            className={`flex w-full items-center gap-3 rounded-xl px-3.5 py-3 text-slate-300 transition hover:bg-white/8 hover:text-white ${
              !sidebarOpen && "justify-center"
            }`}
            title={!sidebarOpen ? "Sair" : undefined}
          >
            <LogOut className="h-5 w-5 shrink-0" />
            {sidebarOpen && <span className="text-sm">Sair</span>}
          </button>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <header className="flex h-14 shrink-0 items-center gap-3 border-b border-slate-200/80 bg-white/90 px-4 backdrop-blur-md lg:hidden">
          <button
            type="button"
            onClick={() => setMobileNavOpen(true)}
            className="rounded-lg p-2 text-slate-600 transition hover:bg-slate-100"
            aria-label="Abrir menu de navegação"
          >
            <Menu className="h-5 w-5" />
          </button>
          <span className="font-display text-lg font-bold text-slate-900">
            Thora
          </span>
        </header>

        <main className="app-canvas min-h-0 flex-1 overflow-y-auto overflow-x-hidden overscroll-y-contain">
          {children}
        </main>
      </div>
    </div>
  );
};

export default SidebarLayout;
