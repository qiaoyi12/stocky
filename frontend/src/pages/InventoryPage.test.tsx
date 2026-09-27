// Tests for InventoryPage routing: clicking an inventory row navigates to the
// Case page at /cases/:sku (Req 14.3). listInventory is mocked to return items;
// the page renders inside a MemoryRouter with a probe Route for /cases/:sku so
// navigation can be observed.
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Routes, Route, useParams } from "react-router-dom";
import InventoryPage from "./InventoryPage";
import type { InventoryItem, InventoryListResponse } from "../api/client";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    listInventory: vi.fn(),
  };
});

import { listInventory } from "../api/client";

const mockedList = vi.mocked(listInventory);

/** Probe component rendered at /cases/:sku so the test can assert which SKU
 * the row click navigated to. */
function CaseProbe() {
  const { sku } = useParams();
  return <div data-testid="case-probe">Case for {sku}</div>;
}

function makeItem(sku: string, name: string): InventoryItem {
  return {
    sku,
    name,
    category: "General",
    current_stock: 10,
    reorder_point: 5,
    lead_time_days: 3,
    unit_cost: 1.5,
    sales_velocity: 2,
    days_of_cover: 5,
    stockout_eta: null,
    no_recent_sales: false,
    classifications: [],
  };
}

describe("InventoryPage routing", () => {
  beforeEach(() => {
    mockedList.mockReset();
  });

  it("navigates to /cases/:sku when an inventory row is clicked", async () => {
    const items: InventoryItem[] = [
      makeItem("SKU-100", "Widget"),
      makeItem("SKU-200", "Gadget"),
    ];
    const response: InventoryListResponse = { items };
    mockedList.mockResolvedValueOnce(response);

    render(
      <MemoryRouter initialEntries={["/inventory"]}>
        <Routes>
          <Route path="/inventory" element={<InventoryPage />} />
          <Route path="/cases/:sku" element={<CaseProbe />} />
        </Routes>
      </MemoryRouter>,
    );

    // Wait for the data to load and rows to render.
    const row = await screen.findByRole("link", {
      name: "Open case for SKU-200",
    });
    await userEvent.click(row);

    const probe = await screen.findByTestId("case-probe");
    expect(probe).toHaveTextContent("Case for SKU-200");
  });
});
