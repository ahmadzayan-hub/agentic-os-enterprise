import express, { type ErrorRequestHandler, type Express } from "express";
import cors from "cors";
import pinoHttp from "pino-http";
import router from "./routes";
import { logger } from "./lib/logger";

const app: Express = express();
app.disable("x-powered-by");

app.use(
  pinoHttp({
    logger,
    serializers: {
      req(req) {
        return {
          id: req.id,
          method: req.method,
          url: req.url?.split("?")[0],
        };
      },
      res(res) {
        return {
          statusCode: res.statusCode,
        };
      },
    },
  }),
);
const configuredOrigins = new Set(
  (process.env.CORS_ALLOWED_ORIGINS ?? "").split(",").map((value) => value.trim()).filter(Boolean),
);
const requestOriginAllowed = (origin: string | undefined, host: string | undefined) => {
  if (!origin) return true;
  try {
    return new URL(origin).host === host || configuredOrigins.has(origin);
  } catch {
    return false;
  }
};
app.use(cors((req, callback) => {
  const origin = req.get("origin");
  const host = req.get("x-forwarded-host") ?? req.get("host");
  callback(null, { origin: requestOriginAllowed(origin, host), credentials: true });
}));
app.use((req, res, next) => {
  if (!["GET", "HEAD", "OPTIONS"].includes(req.method)) {
    const origin = req.get("origin");
    const host = req.get("x-forwarded-host") ?? req.get("host");
    if (!requestOriginAllowed(origin, host)) {
      res.status(403).json({ message: "Request origin is not trusted." });
      return;
    }
  }
  next();
});
app.use(express.json({ limit: "1mb" }));
app.use(express.urlencoded({ extended: true }));

app.use("/api", router);

const errorHandler: ErrorRequestHandler = (error, req, res, _next) => {
  req.log.error({ err: error }, "Unhandled request error");
  if (res.headersSent) return;
  res.status(500).json({ message: "The service could not complete this request.", request_id: req.id });
};
app.use(errorHandler);

export default app;
