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
    await Promise.race([
      pool.query("select 1"),
      new Promise((_, reject) => setTimeout(() => reject(new Error("Database readiness timed out")), 2000)),
    ]);
    res.json({ status: "ready" });
  } catch (error) {
    req.log.error({ err: error }, "Readiness check failed");
    res.status(503).json({ status: "not_ready" });
  }
});

export default router;
