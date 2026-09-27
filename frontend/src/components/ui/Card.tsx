// Generic content container built on the `.card` component class (see
// src/index.css). Optionally renders a header row with a display-font title
// and a right-aligned action slot.
import type { ReactNode } from "react";

interface CardProps {
  title?: string;
  action?: ReactNode;
  className?: string;
  children?: ReactNode;
}

export default function Card({ title, action, className, children }: CardProps) {
  return (
    <div className={`card ${className ?? ""}`.trim()}>
      {(title || action) && (
        <div className="mb-4 flex items-center justify-between gap-3">
          {title ? (
            <h3 className="font-display text-lg text-slate-800">{title}</h3>
          ) : (
            <span />
          )}
          {action ? <div className="shrink-0">{action}</div> : null}
        </div>
      )}
      {children}
    </div>
  );
}
