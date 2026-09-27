// Tests for RecommendationCard: approve/reject controls appear ONLY while the
// recommendation is pending; any other status hides them and shows a status
// message instead (Req 15.3).
import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import RecommendationCard from "./RecommendationCard";
import type {
  RecommendationResponse,
  RecommendationStatus,
} from "../api/client";

function makeRecommendation(
  status: RecommendationStatus,
): RecommendationResponse {
  return {
    id: 1,
    case_id: 1,
    sku: "SKU-1",
    action_kind: "reorder",
    quantity: 50,
    new_reorder_point: null,
    rationale: "Stock is below reorder point.",
    status,
  };
}

describe("RecommendationCard", () => {
  it("renders Approve and Reject controls when status is pending", async () => {
    const onApprove = vi.fn();
    const onReject = vi.fn();

    render(
      <RecommendationCard
        recommendation={makeRecommendation("pending")}
        onApprove={onApprove}
        onReject={onReject}
        busy={false}
      />,
    );

    const approve = screen.getByRole("button", { name: "Approve" });
    const reject = screen.getByRole("button", { name: "Reject" });
    expect(approve).toBeInTheDocument();
    expect(reject).toBeInTheDocument();

    await userEvent.click(approve);
    await userEvent.click(reject);
    expect(onApprove).toHaveBeenCalledTimes(1);
    expect(onReject).toHaveBeenCalledTimes(1);

    // No "no further action" status message while pending.
    expect(
      screen.queryByText(/no further action is available/i),
    ).not.toBeInTheDocument();
  });

  it.each<RecommendationStatus>(["approved", "rejected", "applied"])(
    "hides the controls and shows a status message when status is %s",
    (status) => {
      render(
        <RecommendationCard
          recommendation={makeRecommendation(status)}
          onApprove={vi.fn()}
          onReject={vi.fn()}
          busy={false}
        />,
      );

      expect(
        screen.queryByRole("button", { name: "Approve" }),
      ).not.toBeInTheDocument();
      expect(
        screen.queryByRole("button", { name: "Reject" }),
      ).not.toBeInTheDocument();
      expect(
        screen.getByText(/no further action is available/i),
      ).toBeInTheDocument();
    },
  );
});
