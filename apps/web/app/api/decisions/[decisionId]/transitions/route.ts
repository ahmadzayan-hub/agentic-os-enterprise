import { NextResponse } from "next/server";

import { ApiError, apiFetch } from "@/lib/api";

/**
 * Move a decision.
 *
 * A thin relay: the API decides whether the move is legal and whether this
 * caller may make it. Nothing is validated here beyond reading the form,
 * because a check in this tier could be skipped by posting to the API
 * directly, and a check that can be skipped is decoration.
 */
export async function POST(
  request: Request,
  { params }: { params: Promise<{ decisionId: string }> },
) {
  const { decisionId } = await params;
  const form = await request.formData();
  const toState = String(form.get("to_state") ?? "");
  const reason = String(form.get("reason") ?? "");

  const back = new URL(`/decisions/${decisionId}`, request.url);
  try {
    await apiFetch(`/api/v1/decisions/${decisionId}/transitions`, {
      method: "POST",
      body: JSON.stringify({ to_state: toState, reason }),
    });
    back.searchParams.set("moved", toState);
    return NextResponse.redirect(back, { status: 303 });
  } catch (error) {
    back.searchParams.set(
      "error",
      error instanceof ApiError ? error.message : "The decision could not be moved",
    );
    return NextResponse.redirect(back, { status: 303 });
  }
}
