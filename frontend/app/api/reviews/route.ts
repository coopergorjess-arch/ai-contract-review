import { NextResponse } from "next/server";

const BACKEND = process.env.BACKEND_URL || "http://127.0.0.1:8000";

export async function POST(req: Request) {
  try {
    const form = await req.formData();

    const upstream = await fetch(`${BACKEND}/api/reviews`, {
      method: "POST",
      body: form,
      // 不要手动设 Content-Type，让 fetch 自动带 multipart boundary
    });

    const ct = upstream.headers.get("content-type") || "";
    const payload = ct.includes("application/json") ? await upstream.json() : await upstream.text();

    return NextResponse.json(payload, { status: upstream.status });
  } catch (e: unknown) {
    const message = e instanceof Error ? e.message : String(e);
    return NextResponse.json(
      { detail: `PROXY_FAILED: ${message}` },
      { status: 500 }
    );
  }
}
