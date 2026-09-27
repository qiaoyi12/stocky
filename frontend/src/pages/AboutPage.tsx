const FEATURES = [
  {
    emoji: "🕵️",
    title: "AI Agent Team",
    description: "A multi-agent investigation workflow where agents collaborate to explain what's happening in your inventory.",
  },
  {
    emoji: "🔍",
    title: "Inventory Detection Engine",
    description: "Deterministic stockout, overstock, and slow-mover classification you can trust and audit.",
  },
  {
    emoji: "🧪",
    title: "What-If Simulation",
    description: "Read-only projections that show how demand or lead-time changes would play out, without touching real data.",
  },
  {
    emoji: "🌀",
    title: "Chaos Mode",
    description: "Synthetic disruption testing on a working copy of your data. Source data is never altered.",
  },
  {
    emoji: "🤝",
    title: "Human-in-the-Loop Approval",
    description: "Agents recommend, humans decide. Only approved actions are ever applied.",
  },
  {
    emoji: "💰",
    title: "Cost-of-Overstock Tracking",
    description: "Keep an eye on the capital tied up in excess inventory across your warehouse.",
  },
];

const TECH_STACK = [
  {
    title: "Frontend",
    items: ["React", "Tailwind CSS"],
  },
  {
    title: "Backend",
    items: ["FastAPI", "Python", "SQLite"],
  },
  {
    title: "AI / Agents",
    items: ["LLM gateway via OpenAI-compatible chat completions", "Custom multi-agent orchestration"],
  },
  {
    title: "Deployment",
    items: ["Docker-ready", "Deployable to any container host"],
  },
];

const ARCHITECTURE_STEPS = [
  { emoji: "🖥️", label: "Browser", detail: "React frontend" },
  { emoji: "⚡", label: "FastAPI backend", detail: "REST API + business logic" },
  { emoji: "🤖", label: "AI Agents", detail: "Detective / Forecast / Strategy / Manager" },
  { emoji: "🗄️", label: "SQLite", detail: "Inventory data" },
  { emoji: "🌐", label: "LLM Gateway", detail: "External" },
];

export default function AboutPage() {
  return (
    <div className="space-y-6">
      <div className="card space-y-3">
        <h1 className="font-display text-2xl text-brand-900">Project Brief</h1>
        <p className="text-sm text-slate-600">
          Stocky is an agentic AI-powered inventory management platform that turns static inventory data
          into a live, monitored warehouse. Four AI agents investigate anomalies, forecast demand, and
          propose actions, a human reviewer always approves or rejects before anything changes. What-If
          simulation and Chaos Mode let you explore scenarios before they happen.
        </p>
      </div>

      <div className="card space-y-4">
        <h2 className="font-display text-lg text-brand-900">Key Features</h2>
        <div className="grid gap-4 sm:grid-cols-2">
          {FEATURES.map((feature) => (
            <div key={feature.title} className="rounded-2xl border border-slate-100 p-4 shadow-card">
              <div className="flex items-center gap-2">
                <span aria-hidden="true" className="text-xl">
                  {feature.emoji}
                </span>
                <h3 className="font-display text-base text-slate-700">{feature.title}</h3>
              </div>
              <p className="mt-2 text-sm text-slate-600">{feature.description}</p>
            </div>
          ))}
        </div>
      </div>

      <div className="card space-y-4">
        <h2 className="font-display text-lg text-brand-900">Tech Stack</h2>
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {TECH_STACK.map((group) => (
            <div key={group.title} className="rounded-2xl border border-slate-100 p-4 shadow-card">
              <h3 className="font-display text-base text-brand-700">{group.title}</h3>
              <ul className="mt-2 space-y-1 text-sm text-slate-600">
                {group.items.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </div>

      <div className="card space-y-4">
        <h2 className="font-display text-lg text-brand-900">Architecture</h2>
        <div className="flex flex-wrap items-center gap-3">
          {ARCHITECTURE_STEPS.map((step, index) => (
            <div key={step.label} className="flex items-center gap-3">
              <div className="rounded-2xl border border-brand-200 bg-brand-50 px-4 py-3 text-center shadow-card">
                <div aria-hidden="true" className="text-xl">
                  {step.emoji}
                </div>
                <div className="font-display text-sm text-brand-900">{step.label}</div>
                <div className="text-xs text-slate-500">{step.detail}</div>
              </div>
              {index < ARCHITECTURE_STEPS.length - 1 && (
                <span aria-hidden="true" className="text-lg text-brand-400">
                  →
                </span>
              )}
            </div>
          ))}
        </div>
        <p className="text-sm text-slate-600">
          Safety boundary: agents never write directly to the database. Only human-approved actions
          trigger deterministic backend writes.
        </p>
      </div>
    </div>
  );
}
