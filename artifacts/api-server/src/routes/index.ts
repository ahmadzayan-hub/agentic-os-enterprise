import { Router, type IRouter } from "express";
import healthRouter from "./health";
import v1Router from "./v1";

const router: IRouter = Router();

router.use(healthRouter);
router.use(v1Router);

export default router;
