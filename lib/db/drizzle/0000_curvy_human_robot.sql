CREATE TABLE IF NOT EXISTS "agentic_audit_events" (
	"id" text PRIMARY KEY NOT NULL,
	"sequence_no" text NOT NULL,
	"tenant_id" text NOT NULL,
	"actor_user_id" text,
	"category" text NOT NULL,
	"action" text NOT NULL,
	"outcome" text NOT NULL,
	"resource_type" text NOT NULL,
	"resource_id" text NOT NULL,
	"detail" jsonb NOT NULL,
	"previous_hash" text NOT NULL,
	"entry_hash" text NOT NULL,
	"agent_id" text,
	"tool_id" text,
	"occurred_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "agentic_records" (
	"tenant_id" text NOT NULL,
	"resource_type" text NOT NULL,
	"resource_id" text NOT NULL,
	"data" jsonb NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "agentic_sessions" (
	"token_hash" text PRIMARY KEY NOT NULL,
	"user_id" text NOT NULL,
	"expires_at" timestamp with time zone NOT NULL,
	"mfa_satisfied" boolean DEFAULT false NOT NULL,
	"revoked_at" timestamp with time zone,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "agentic_tenants" (
	"id" text PRIMARY KEY NOT NULL,
	"name" text NOT NULL,
	"slug" text NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "agentic_tenants_slug_unique" UNIQUE("slug")
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS "agentic_users" (
	"id" text PRIMARY KEY NOT NULL,
	"tenant_id" text NOT NULL,
	"email" text NOT NULL,
	"display_name" text NOT NULL,
	"password_salt" text NOT NULL,
	"password_hash" text NOT NULL,
	"roles" text[] NOT NULL,
	"permissions" text[] NOT NULL,
	"clearance" text NOT NULL,
	"active" boolean DEFAULT true NOT NULL,
	"last_login_at" timestamp with time zone,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL
);
--> statement-breakpoint
DO $$ BEGIN
 ALTER TABLE "agentic_audit_events" ADD CONSTRAINT "agentic_audit_events_tenant_id_agentic_tenants_id_fk" FOREIGN KEY ("tenant_id") REFERENCES "public"."agentic_tenants"("id") ON DELETE no action ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;--> statement-breakpoint
DO $$ BEGIN
 ALTER TABLE "agentic_audit_events" ADD CONSTRAINT "agentic_audit_events_actor_user_id_agentic_users_id_fk" FOREIGN KEY ("actor_user_id") REFERENCES "public"."agentic_users"("id") ON DELETE no action ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;--> statement-breakpoint
DO $$ BEGIN
 ALTER TABLE "agentic_records" ADD CONSTRAINT "agentic_records_tenant_id_agentic_tenants_id_fk" FOREIGN KEY ("tenant_id") REFERENCES "public"."agentic_tenants"("id") ON DELETE no action ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;--> statement-breakpoint
DO $$ BEGIN
 ALTER TABLE "agentic_sessions" ADD CONSTRAINT "agentic_sessions_user_id_agentic_users_id_fk" FOREIGN KEY ("user_id") REFERENCES "public"."agentic_users"("id") ON DELETE no action ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;--> statement-breakpoint
DO $$ BEGIN
 ALTER TABLE "agentic_users" ADD CONSTRAINT "agentic_users_tenant_id_agentic_tenants_id_fk" FOREIGN KEY ("tenant_id") REFERENCES "public"."agentic_tenants"("id") ON DELETE no action ON UPDATE no action;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS "agentic_records_identity" ON "agentic_records" USING btree ("tenant_id","resource_type","resource_id");--> statement-breakpoint
CREATE UNIQUE INDEX IF NOT EXISTS "agentic_users_tenant_email" ON "agentic_users" USING btree ("tenant_id","email");