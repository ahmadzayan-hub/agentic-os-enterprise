import { Router, type IRouter } from "express";
import { HealthCheckResponse } from "@workspace/api-zod";
import { pool } from "@workspace/db";

const router: IRouter = Router();

router.get("/healthz", (_req, res) => {
  const data = HealthCheckResponse.parse({ status: "ok" });
  res.json(data);
});

router.get("/readyz", async (req, res) => {
  try {
    const result = await Promise.race([
      pool.query<{ schema_ready: boolean }>(`
        select
          to_regclass('public.agentic_tenants') is not null
          and to_regclass('public.agentic_users') is not null
          and to_regclass('public.agentic_sessions') is not null
          and to_regclass('public.agentic_records') is not null
          and to_regclass('public.agentic_audit_events') is not null
          and to_regclass('drizzle.__drizzle_migrations') is not null
          as schema_ready
      `),
      new Promise<never>((_, reject) => setTimeout(() => reject(new Error("Database readiness timed out")), 2000)),
    ]);
    if (!result.rows[0]?.schema_ready) throw new Error("Required database schema is not migrated");
    res.json({ status: "ready" });
  } catch (error) {
    req.log.error({ err: error }, "Readiness check failed");
    res.status(503).json({ status: "not_ready" });
  }
});

export default router;
