import type { CaseResponse } from "../api/client";
import { AGENT_PERSONAS, colorClasses } from "../lib/agentPersonas";

// Renders the four sequential agent outputs of an investigation case, or an
// error state when the case failed (Req 15.1).
//
// On success each of Detective / Forecast / Strategy / Manager is shown as a
// labelled step with its produced text, alongside its cute persona (name +
// emoji, e.g. "Pip · Detective"). When the case status is "error" the failed
// step is highlighted instead. When still "running" an in-progress indicator
// is shown. The parent (CasePage) owns fetching and the recommendation card;
// this component is presentational.

interface AgentCaseProps {
  agentCase: CaseResponse;
}

interface Step {
  key: string;
  label: string;
  output?: string | null;
}

export default function AgentCase({ agentCase }: AgentCaseProps) {
  const {
    status,
    failed_step,
    detective_out,
    forecast_out,
    strategy_out,
    manager_out,
  } = agentCase;

  const steps: Step[] = [
    { key: "detective", label: "Detective", output: detective_out },
    { key: "forecast", label: "Forecast", output: forecast_out },
    { key: "strategy", label: "Strategy", output: strategy_out },
    { key: "manager", label: "Manager", output: manager_out },
  ];

  if (status === "error") {
    return (
      <section className="card border-rose-200 bg-rose-50">
        <h2 className="font-display text-lg text-rose-800">
          Investigation failed
        </h2>
        <p className="mt-1 text-sm text-rose-700">
          The investigation stopped
          {failed_step ? (
            <>
              {" "}at the{" "}
              <span className="font-semibold capitalize">{failed_step}</span>{" "}
              step.
            </>
          ) : (
            "."
          )}
        </p>

        {/* Show whatever steps completed before the failure. */}
        <ol className="mt-4 space-y-4">
          {steps.map((step) => (
            <StepItem
              key={step.key}
              stepKey={step.key}
              label={step.label}
              output={step.output}
              failed={failed_step === step.key}
            />
          ))}
        </ol>
      </section>
    );
  }

  return (
    <section className="card">
      <div className="flex items-center justify-between">
        <h2 className="font-display text-lg text-slate-900">
          Agent investigation
        </h2>
        {status === "running" && (
          <span className="pill bg-sky-100 text-sky-800">In progress…</span>
        )}
      </div>

      <ol className="mt-4 space-y-4">
        {steps.map((step) => (
          <StepItem
            key={step.key}
            stepKey={step.key}
            label={step.label}
            output={step.output}
            running={status === "running"}
          />
        ))}
      </ol>
    </section>
  );
}

interface StepItemProps {
  stepKey: string;
  label: string;
  output?: string | null;
  failed?: boolean;
  running?: boolean;
}

function StepItem({ stepKey, label, output, failed, running }: StepItemProps) {
  const persona = AGENT_PERSONAS[stepKey];

  return (
    <li
      className={`rounded-2xl border p-4 ${
        failed ? "border-rose-300 bg-rose-100/50" : "border-slate-100 bg-slate-50"
      }`}
    >
      <div className="flex items-center gap-2">
        {persona ? (
          <span
            className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-base ${colorClasses(
              persona.color,
            )}`}
            aria-hidden="true"
          >
            {persona.emoji}
          </span>
        ) : (
          <span
            className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-slate-200 text-xs font-semibold text-slate-700"
            aria-hidden="true"
          >
            {label.charAt(0)}
          </span>
        )}
        <h3 className="text-sm font-semibold text-slate-900">
          {persona ? `${persona.name} · ${label}` : label}
        </h3>
        {failed && (
          <span className="pill bg-rose-200 text-rose-800">failed</span>
        )}
      </div>
      {output ? (
        <p className="mt-2 whitespace-pre-wrap text-sm text-slate-700">
          {output}
        </p>
      ) : (
        <p className="mt-2 text-sm italic text-slate-400">
          {running ? "Waiting for output…" : "No output."}
        </p>
      )}
    </li>
  );
}
