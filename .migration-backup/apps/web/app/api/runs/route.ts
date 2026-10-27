import { NextResponse } from "next/server";

import { ApiError, apiFetch } from "@/lib/api";

/** Server-side proxy so the browser never holds an API token. */
export async function POST(request: Request) {
  const body = await request.json();
  try {
    const result = await apiFetch<Record<string, unknown>>("/api/v1/runs", {
      method: "POST",
      body: JSON.stringify(body),
    });
    return NextResponse.json(result);
  } catch (error) {
    if (error instanceof ApiError) {
      return NextResponse.json(
        { message: error.message, payload: error.payload },
        { status: error.status },
      );
    }
    return NextResponse.json({ message: "Unexpected error" }, { status: 500 });
  }
}
