import { ApiError, apiFetch } from "@/lib/api";
import { redirectTo } from "@/lib/redirect";

/**
 * Acknowledge an alert.
 *
 * A form POST rather than client-side fetch, so acknowledgement works without
 * JavaScript and so the access token stays in the httpOnly cookie the server
 * reads — the browser never holds it.
 *
 * The API decides whether this caller may see the alert at all and attributes
 * the acknowledgement to the authenticated session. Nothing here is trusted to
 * say who acted: the alert id is the only thing this route forwards.
 */
export async function POST(
  request: Request,
  { params }: { params: Promise<{ alertId: string }> },
) {
  const { alertId } = await params;
  try {
    await apiFetch(`/api/v1/alerts/${alertId}/acknowledge`, { method: "POST" });
    return redirectTo("/operations/alerts", { acknowledged: "1" });
  } catch (error) {
    return redirectTo("/operations/alerts", {
      error:
        error instanceof ApiError ? error.message : "Acknowledgement failed",
    });
  }
}
