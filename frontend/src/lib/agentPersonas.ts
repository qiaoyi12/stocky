// Cute persona metadata for the four backend agent steps. Keyed by the exact
// step names used by CaseResponse / AgentCase ("detective", "forecast",
// "strategy", "manager") so callers can look personas up directly from
// backend data without any string mapping.

export type PersonaColor = "brand" | "sky" | "purple" | "amber";

export interface Persona {
  name: string;
  role: string;
  emoji: string;
  color: PersonaColor;
}

export const AGENT_PERSONAS: Record<string, Persona> = {
  detective: { name: "Pip", role: "Detective", emoji: "🕵️", color: "sky" },
  forecast: { name: "Mimi", role: "Forecaster", emoji: "🔮", color: "purple" },
  strategy: { name: "Bibi", role: "Strategist", emoji: "🛒", color: "amber" },
  manager: { name: "Stocky", role: "Manager", emoji: "🐰", color: "brand" },
};

/** Fixed display order for the four agent steps. */
export const PERSONA_ORDER: string[] = [
  "detective",
  "forecast",
  "strategy",
  "manager",
];

/** Tailwind background/text classes for a persona's avatar bubble. */
export function colorClasses(color: PersonaColor): string {
  switch (color) {
    case "brand":
      return "bg-brand-100 text-brand-700";
    case "sky":
      return "bg-sky-100 text-sky-700";
    case "purple":
      return "bg-purple-100 text-purple-700";
    case "amber":
      return "bg-amber-100 text-amber-700";
    default:
      return "bg-slate-100 text-slate-700";
  }
}
