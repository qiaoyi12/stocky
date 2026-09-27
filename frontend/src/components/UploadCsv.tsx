// CSV upload widget for the Dashboard.
//
// Presents a file input plus an upload button. On submit it POSTs the selected
// file to the backend via `uploadInventory` (multipart) and renders the result:
// accepted count, rejected count, an optional missing-column notice, and the
// per-row rejection reasons. On a successful upload it invokes the optional
// `onUploaded` callback so the parent (Dashboard) can refresh its aggregates.
//
// Requirement 1.5: surface accepted/rejected counts and rejection reasons.

import { useRef, useState } from "react";
import { ApiError, uploadInventory, type UploadResult } from "../api/client";

interface UploadCsvProps {
  /** Called after a successful upload so the parent can refresh its data. */
  onUploaded?: (result: UploadResult) => void;
}

export default function UploadCsv({ onUploaded }: UploadCsvProps) {
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [result, setResult] = useState<UploadResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  async function handleUpload() {
    if (!file || uploading) {
      return;
    }
    setUploading(true);
    setError(null);
    setResult(null);
    try {
      const res = await uploadInventory(file);
      setResult(res);
      // Reset the file selection so the same file can be re-picked if needed.
      setFile(null);
      if (inputRef.current) {
        inputRef.current.value = "";
      }
      onUploaded?.(res);
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.message
          : "Upload failed. Please try again.",
      );
    } finally {
      setUploading(false);
    }
  }

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-4 shadow-sm">
      <h2 className="text-lg font-semibold">Upload inventory CSV</h2>
      <p className="mt-1 text-sm text-slate-500">
        Upload a CSV to ingest SKUs and recompute conditions.
      </p>

      <div className="mt-3 flex flex-wrap items-center gap-3">
        <input
          ref={inputRef}
          type="file"
          accept=".csv,text/csv"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          disabled={uploading}
          className="block text-sm text-slate-700 file:mr-3 file:rounded-md file:border-0 file:bg-slate-100 file:px-3 file:py-2 file:text-sm file:font-medium file:text-slate-700 hover:file:bg-slate-200"
        />
        <button
          type="button"
          onClick={handleUpload}
          disabled={!file || uploading}
          className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {uploading ? "Uploading…" : "Upload"}
        </button>
      </div>

      {error && (
        <p className="mt-3 rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
          {error}
        </p>
      )}

      {result && (
        <div className="mt-4 space-y-3">
          <div className="flex flex-wrap gap-4">
            <div className="rounded-md bg-emerald-50 px-3 py-2">
              <span className="text-2xl font-semibold text-emerald-700">
                {result.accepted}
              </span>
              <span className="ml-2 text-sm text-emerald-700">accepted</span>
            </div>
            <div className="rounded-md bg-amber-50 px-3 py-2">
              <span className="text-2xl font-semibold text-amber-700">
                {result.rejected_count}
              </span>
              <span className="ml-2 text-sm text-amber-700">rejected</span>
            </div>
          </div>

          {result.missing_column && (
            <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700">
              Upload rejected — missing required column:{" "}
              <span className="font-mono font-semibold">
                {result.missing_column}
              </span>
            </p>
          )}

          {result.rejected.length > 0 && (
            <div>
              <h3 className="text-sm font-semibold text-slate-700">
                Rejected rows
              </h3>
              <ul className="mt-1 max-h-48 overflow-auto rounded-md border border-slate-200 text-sm">
                {result.rejected.map((r, i) => (
                  <li
                    key={`${r.row}-${i}`}
                    className="flex gap-3 border-b border-slate-100 px-3 py-1.5 last:border-b-0"
                  >
                    <span className="font-mono text-slate-500">
                      row {r.row}
                    </span>
                    <span className="text-slate-700">{r.reason}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
