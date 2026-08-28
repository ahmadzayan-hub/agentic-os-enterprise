import { NextResponse } from "next/server";

import { ApiError, apiFetch } from "@/lib/api";

export async function POST(
  request: Request,
  { params }: { params: Promise<{ approvalId: string }> },
) {
  const { approvalId } = await params;
  const form = await request.formData();
  const decision = String(form.get("decision") ?? "");
  const comment = String(form.get("comment") ?? "");

  try {
    await apiFetch(`/api/v1/approvals/${approvalId}/decide`, {
      method: "POST",
      body: JSON.stringify({ decision, comment }),
    });
    return NextResponse.redirect(new URL("/approvals?decided=1", request.url), {
      status: 303,
    });
  } catch (error) {
    const message = error instanceof ApiError ? error.message : "Decision failed";
    const url = new URL("/approvals", request.url);
    url.searchParams.set("error", message);
    return NextResponse.redirect(url, { status: 303 });
  }
}
