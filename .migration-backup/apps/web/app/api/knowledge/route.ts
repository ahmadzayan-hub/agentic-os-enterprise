import { NextResponse } from "next/server";

import { ApiError, apiFetch } from "@/lib/api";

export async function POST(request: Request) {
  const body = await request.json();
  try {
    return NextResponse.json(
      await apiFetch("/api/v1/knowledge/search", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    );
  } catch (error) {
    if (error instanceof ApiError) {
      return NextResponse.json({ message: error.message }, { status: error.status });
    }
    return NextResponse.json({ message: "Unexpected error" }, { status: 500 });
  }
}
