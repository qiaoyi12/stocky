// Tests for UploadCsv: after selecting a file and clicking Upload, the widget
// displays the accepted/rejected counts and per-row rejection reasons returned
// by the (mocked) uploadInventory API call (Req 1.5).
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import UploadCsv from "./UploadCsv";
import type { UploadResult } from "../api/client";

// Mock the API client module so no real network call is made.
vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    uploadInventory: vi.fn(),
  };
});

import { uploadInventory } from "../api/client";

const mockedUpload = vi.mocked(uploadInventory);

describe("UploadCsv", () => {
  beforeEach(() => {
    mockedUpload.mockReset();
  });

  it("shows accepted/rejected counts and rejection reasons after upload", async () => {
    const result: UploadResult = {
      accepted: 3,
      rejected_count: 2,
      rejected: [
        { row: 4, reason: "current_stock is not numeric" },
        { row: 7, reason: "reorder_point is not numeric" },
      ],
      missing_column: null,
    };
    mockedUpload.mockResolvedValueOnce(result);

    render(<UploadCsv />);

    const file = new File(["sku,name\nA,Widget\n"], "inventory.csv", {
      type: "text/csv",
    });
    const input = screen.getByRole("button", { name: "Upload" });

    // Select the file via the file input, then click Upload.
    const fileInput = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    await userEvent.upload(fileInput, file);
    await userEvent.click(input);

    // Counts render.
    expect(await screen.findByText("3")).toBeInTheDocument();
    expect(screen.getByText("accepted")).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText("rejected")).toBeInTheDocument();

    // Rejected row reasons render.
    expect(
      screen.getByText("current_stock is not numeric"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("reorder_point is not numeric"),
    ).toBeInTheDocument();
    expect(screen.getByText("row 4")).toBeInTheDocument();
    expect(screen.getByText("row 7")).toBeInTheDocument();

    expect(mockedUpload).toHaveBeenCalledTimes(1);
    expect(mockedUpload).toHaveBeenCalledWith(file);
  });

  it("shows a missing-column notice when the upload was rejected wholesale", async () => {
    const result: UploadResult = {
      accepted: 0,
      rejected_count: 0,
      rejected: [],
      missing_column: "reorder_point",
    };
    mockedUpload.mockResolvedValueOnce(result);

    render(<UploadCsv />);

    const file = new File(["sku,name\n"], "bad.csv", { type: "text/csv" });
    const fileInput = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    await userEvent.upload(fileInput, file);
    await userEvent.click(screen.getByRole("button", { name: "Upload" }));

    expect(
      await screen.findByText(/missing required column/i),
    ).toBeInTheDocument();
    expect(screen.getByText("reorder_point")).toBeInTheDocument();
  });
});
