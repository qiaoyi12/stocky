// Datasets / uploads page.
//
// On mount it fetches the upload history (`listUploads`) and renders it as a
// simple list (filename, uploaded_at, sku_count). A single delete button at
// the top calls `deleteAllUploads`, which wipes all uploaded inventory data
// back to empty; on success the displayed list is cleared. There is no
// per-dataset switching here — uploading a new CSV always replaces the
// current inventory outright, so this page is a history log plus a full
// reset action, not a dataset switcher.

import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  deleteAllUploads,
  listUploads,
  type UploadSummary,
} from "../api/client";

export default function DatasetsPage() {
  const [uploads, setUploads] = useState<UploadSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);

  const loadUploads = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setUploads(await listUploads());
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Failed to load uploads.",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadUploads();
  }, [loadUploads]);

  const handleDeleteAll = useCallback(async () => {
    setDeleting(true);
    setError(null);
    try {
      await deleteAllUploads();
      setUploads([]);
    } catch (err) {
      setError(
        err instanceof ApiError ? err.message : "Failed to delete uploaded data.",
      );
    } finally {
      setDeleting(false);
    }
  }, []);

  return (
    <section className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="font-display text-2xl text-slate-900">
          Datasets <span aria-hidden="true">🗂️</span>
        </h1>
        <button
          type="button"
          onClick={() => void handleDeleteAll()}
          disabled={deleting || uploads.length === 0}
          className="rounded-xl bg-red-600 px-4 py-2 text-sm font-semibold text-white transition hover:bg-red-700 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {deleting ? "Deleting…" : "Delete all uploaded data"}
        </button>
      </div>

      {loading && (
        <p role="status" className="text-sm text-slate-500">
          Loading uploads…
        </p>
      )}

      {error && (
        <div
          className="rounded-2xl border border-red-200 bg-red-50 px-4 py-3 text-red-800"
          role="alert"
        >
          {error}{" "}
          <button
            type="button"
            onClick={() => void loadUploads()}
            className="font-medium underline"
          >
            Retry
          </button>
        </div>
      )}

      {!loading && !error && uploads.length === 0 && (
        <p className="text-sm text-slate-500">
          No uploads yet. Upload a CSV from the Inventory page to get started.
        </p>
      )}

      {!loading && !error && uploads.length > 0 && (
        <div className="card overflow-hidden p-0">
          <table className="w-full text-left text-sm">
            <thead className="bg-slate-50 text-slate-500">
              <tr>
                <th className="px-4 py-3 font-medium">Filename</th>
                <th className="px-4 py-3 font-medium">Uploaded at</th>
                <th className="px-4 py-3 font-medium">SKU count</th>
              </tr>
            </thead>
            <tbody>
              {uploads.map((upload) => (
                <tr key={upload.id} className="border-t border-slate-100">
                  <td className="px-4 py-3 text-slate-900">{upload.filename}</td>
                  <td className="px-4 py-3 text-slate-600">{upload.uploaded_at}</td>
                  <td className="px-4 py-3 text-slate-600">{upload.sku_count}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
