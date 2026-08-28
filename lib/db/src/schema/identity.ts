import {
  boolean,
  jsonb,
  pgTable,
  text,
  timestamp,
  uniqueIndex,
} from "drizzle-orm/pg-core";

export const tenantsTable = pgTable("agentic_tenants", {
  id: text("id").primaryKey(),
  name: text("name").notNull(),
  slug: text("slug").notNull().unique(),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
});

export const usersTable = pgTable(
  "agentic_users",
  {
    id: text("id").primaryKey(),
    tenantId: text("tenant_id").notNull().references(() => tenantsTable.id),
    email: text("email").notNull(),
    displayName: text("display_name").notNull(),
    passwordSalt: text("password_salt").notNull(),
    passwordHash: text("password_hash").notNull(),
    roles: text("roles").array().notNull(),
    permissions: text("permissions").array().notNull(),
    clearance: text("clearance").notNull(),
    active: boolean("active").notNull().default(true),
    lastLoginAt: timestamp("last_login_at", { withTimezone: true }),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (table) => [uniqueIndex("agentic_users_tenant_email").on(table.tenantId, table.email)],
);

export const sessionsTable = pgTable("agentic_sessions", {
  tokenHash: text("token_hash").primaryKey(),
  userId: text("user_id").notNull().references(() => usersTable.id),
  expiresAt: timestamp("expires_at", { withTimezone: true }).notNull(),
  mfaSatisfied: boolean("mfa_satisfied").notNull().default(false),
  revokedAt: timestamp("revoked_at", { withTimezone: true }),
  createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
});

export const recordsTable = pgTable(
  "agentic_records",
  {
    tenantId: text("tenant_id").notNull().references(() => tenantsTable.id),
    resourceType: text("resource_type").notNull(),
    resourceId: text("resource_id").notNull(),
    data: jsonb("data").notNull(),
    createdAt: timestamp("created_at", { withTimezone: true }).notNull().defaultNow(),
    updatedAt: timestamp("updated_at", { withTimezone: true }).notNull().defaultNow(),
  },
  (table) => [
    uniqueIndex("agentic_records_identity").on(
      table.tenantId,
      table.resourceType,
      table.resourceId,
    ),
  ],
);

export const auditEventsTable = pgTable("agentic_audit_events", {
  id: text("id").primaryKey(),
  sequenceNo: text("sequence_no").notNull(),
  tenantId: text("tenant_id").notNull().references(() => tenantsTable.id),
  actorUserId: text("actor_user_id").references(() => usersTable.id),
  category: text("category").notNull(),
  action: text("action").notNull(),
  outcome: text("outcome").notNull(),
  resourceType: text("resource_type").notNull(),
  resourceId: text("resource_id").notNull(),
  detail: jsonb("detail").notNull(),
  previousHash: text("previous_hash").notNull(),
  entryHash: text("entry_hash").notNull(),
  agentId: text("agent_id"),
  toolId: text("tool_id"),
  occurredAt: timestamp("occurred_at", { withTimezone: true }).notNull().defaultNow(),
});