import { ApiError, apiFetch } from "@/lib/api";
import { redirectTo } from "@/lib/redirect";

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
    return redirectTo("/approvals", { decided: "1" });
  } catch (error) {
    return redirectTo("/approvals", {
      error: error instanceof ApiError ? error.message : "Decision failed",
    });
  }
}
