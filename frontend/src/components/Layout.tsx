// Persistent app shell for the Stocky frontend. Owns the left sidebar (brand,
// navigation, mascot helper) and the main column chrome (sticky top bar with a
// decorative search box, notification bell and user chip). Page content renders
// through react-router's <Outlet/>.
import { NavLink, Outlet } from "react-router-dom";

interface NavItem {
  label: string;
  emoji: string;
  to: string;
}

// Only routes that map to a real page — no dead links.
const NAV_ITEMS: NavItem[] = [
  { label: "Home", emoji: "🏠", to: "/" },
  { label: "Inventory", emoji: "📦", to: "/inventory" },
  { label: "AI Agents", emoji: "🤖", to: "/agents" },
  { label: "Simulations", emoji: "🧬", to: "/simulations" },
  { label: "Warehouse", emoji: "🏭", to: "/warehouse" },
  { label: "What-If Lab", emoji: "🧪", to: "/chaos" },
  { label: "Reports", emoji: "📊", to: "/impact" },
  { label: "Datasets", emoji: "🗂️", to: "/datasets" },
  { label: "About", emoji: "ℹ️", to: "/about" },
];

const navLinkClass = ({ isActive }: { isActive: boolean }) =>
  [
    "flex items-center gap-3 rounded-xl px-3 py-2 text-sm font-semibold transition",
    isActive
      ? "bg-brand-50 text-brand-700"
      : "text-slate-600 hover:bg-slate-100",
  ].join(" ");

export default function Layout() {
  return (
    <div className="min-h-screen text-slate-800">
      <div className="mx-auto flex max-w-7xl gap-6 p-4 md:p-6">
        {/* Sidebar */}
        <aside className="hidden w-[248px] shrink-0 flex-col md:flex">
          <div className="flex h-full flex-col rounded-2xl border border-slate-100 bg-white p-4 shadow-card">
            {/* Brand */}
            <div className="px-2 py-2">
              <div className="flex items-center gap-2">
                <span className="text-2xl" aria-hidden="true">
                  🐰
                </span>
                <span className="font-display text-xl text-slate-800">
                  Stocky
                </span>
              </div>
              <p className="mt-1 text-[11px] leading-snug text-slate-400">
                Don't manage the inventory. Survive it.
              </p>
            </div>

            {/* Nav */}
            <nav className="mt-4 flex flex-col gap-1">
              {NAV_ITEMS.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.to === "/"}
                  className={navLinkClass}
                >
                  <span className="text-base" aria-hidden="true">
                    {item.emoji}
                  </span>
                  <span>{item.label}</span>
                </NavLink>
              ))}
            </nav>

            {/* Mascot helper card */}
            <div className="mt-auto rounded-2xl bg-brand-50 p-3">
              <div className="flex items-start gap-2">
                <span className="text-lg" aria-hidden="true">
                  🐰
                </span>
                <p className="text-[11px] leading-snug text-brand-800">
                  Hey! I'm Stocky! Your AI inventory assistant. Let's keep your
                  warehouse healthy! 💜
                </p>
              </div>
            </div>
          </div>
        </aside>

        {/* Main column */}
        <div className="flex min-w-0 flex-1 flex-col">
          {/* Top bar */}
          <header className="sticky top-0 z-10 mb-6 flex items-center gap-3 rounded-2xl border border-slate-100 bg-white/90 px-4 py-3 shadow-card backdrop-blur">
            <div className="relative flex-1">
              <span
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400"
                aria-hidden="true"
              >
                🔍
              </span>
              <input
                type="search"
                placeholder="Search products, categories…"
                aria-label="Search products and categories"
                className="w-full rounded-full border border-slate-200 bg-slate-50 py-2 pl-9 pr-4 text-sm text-slate-700 placeholder:text-slate-400 focus:border-brand-300 focus:outline-none focus:ring-2 focus:ring-brand-100"
              />
            </div>
            <button
              type="button"
              aria-label="Notifications"
              className="flex h-10 w-10 items-center justify-center rounded-full border border-slate-200 bg-white text-lg hover:bg-slate-50"
            >
              <span aria-hidden="true">🔔</span>
            </button>
            <div className="flex items-center gap-2 rounded-full border border-slate-200 bg-white py-1 pl-1 pr-3">
              <span
                className="flex h-8 w-8 items-center justify-center rounded-full bg-brand-100 text-sm font-semibold text-brand-700"
                aria-hidden="true"
              >
                SA
              </span>
              <span className="text-sm font-semibold text-slate-700">
                SOC Analyst
              </span>
            </div>
          </header>

          {/* Routed page content */}
          <main className="min-w-0">
            <Outlet />
          </main>
        </div>
      </div>
    </div>
  );
}
