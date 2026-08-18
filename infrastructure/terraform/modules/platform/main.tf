# Platform substrate for one environment.
#
# This module provisions what the workloads need to exist *before* they are
# deployed: the namespace and its guardrails, the database roles the migration
# and the application connect as, and the Kubernetes secrets that carry the
# connection strings. The workloads themselves are Kubernetes manifests under
# infrastructure/kubernetes, applied by the delivery pipeline after this module
# has converged. Keeping them apart means a workload rollback never rolls back
# a database role, and vice versa.

locals {
  labels = {
    "app.kubernetes.io/part-of" = "agentic-os"
    "app.kubernetes.io/managed-by" = "terraform"
    "agentic.rta/environment"      = var.environment
  }

  database_url = format(
    "postgresql+psycopg://agentic_app:%s@%s:%d/%s",
    var.database_app_password, var.database_host, var.database_port, var.database_name,
  )
  database_owner_url = format(
    "postgresql+psycopg://agentic_owner:%s@%s:%d/%s",
    var.database_owner_password, var.database_host, var.database_port, var.database_name,
  )
  dr_admin_url = format(
    "postgresql+psycopg://agentic_maintenance:%s@%s:%d/postgres",
    var.database_maintenance_password, var.database_host, var.database_port,
  )
}

resource "kubernetes_namespace_v1" "platform" {
  metadata {
    name = var.namespace
    labels = merge(local.labels, {
      # Restricted Pod Security: no privileged containers, no host namespaces,
      # non-root only. The images are built to satisfy it.
      "pod-security.kubernetes.io/enforce" = "restricted"
      "pod-security.kubernetes.io/audit"   = "restricted"
      "pod-security.kubernetes.io/warn"    = "restricted"
    })
  }
}

resource "kubernetes_resource_quota_v1" "platform" {
  metadata {
    name      = "agentic-os"
    namespace = kubernetes_namespace_v1.platform.metadata[0].name
  }
  spec {
    hard = {
      "requests.cpu"    = var.resource_quota.cpu
      "requests.memory" = var.resource_quota.memory
      "pods"            = var.resource_quota.pods
    }
  }
}

resource "kubernetes_limit_range_v1" "platform" {
  metadata {
    name      = "agentic-os"
    namespace = kubernetes_namespace_v1.platform.metadata[0].name
  }
  spec {
    limit {
      type = "Container"
      default = {
        cpu    = "500m"
        memory = "512Mi"
      }
      default_request = {
        cpu    = "100m"
        memory = "128Mi"
      }
    }
  }
}

# ---------------------------------------------------------------------------
# Database roles.
#
# These mirror database/bootstrap/00_cluster_bootstrap.sql, which exists for
# local development where there is no Terraform. The privilege split is the
# point and is asserted here as well as there: the application role may never
# bypass row level security, and the one role that may is NOLOGIN, so it is
# reachable only through SET ROLE inside a SECURITY DEFINER function.
# ---------------------------------------------------------------------------
resource "postgresql_role" "provisioner" {
  name        = "agentic_provisioner"
  login       = false
  bypass_row_level_security = true
  create_database = false
  create_role     = false
}

resource "postgresql_role" "owner" {
  name     = "agentic_owner"
  login    = true
  password = var.database_owner_password
  bypass_row_level_security = false
  create_database = false
  create_role     = false
  roles           = [postgresql_role.provisioner.name]
}

resource "postgresql_role" "app" {
  name     = "agentic_app"
  login    = true
  password = var.database_app_password
  bypass_row_level_security = false
  superuser       = false
  create_database = false
  create_role     = false
}

# The break-glass identity for the restore exercise. It needs CREATE DATABASE
# because a restore target has to be created, and it is the only role with it.
resource "postgresql_role" "maintenance" {
  count    = var.enable_dr_exercise ? 1 : 0
  name     = "agentic_maintenance"
  login    = true
  password = var.database_maintenance_password
  create_database = true
  create_role     = false
  roles           = [postgresql_role.owner.name]
}

# ---------------------------------------------------------------------------
# Secrets delivered to the workloads.
# ---------------------------------------------------------------------------
resource "kubernetes_secret_v1" "application" {
  metadata {
    name      = "agentic-secrets"
    namespace = kubernetes_namespace_v1.platform.metadata[0].name
    labels    = local.labels
  }
  type = "Opaque"
  data = {
    AGENTIC_DATABASE_URL       = local.database_url
    AGENTIC_DATABASE_OWNER_URL = local.database_owner_url
    AGENTIC_JWT_SECRET         = var.jwt_secret
    AGENTIC_KMS_LOCAL_KEY      = var.kms_local_key
    AGENTIC_KMS_BACKEND        = var.kms_backend
    AGENTIC_SECRET_BACKEND     = var.secret_backend
    AGENTIC_POLICY_MODE        = var.policy_mode
  }
}

# Mounted only by the disaster recovery CronJob. Splitting it out is what keeps
# the API and the worker from ever holding an identity that can create a
# database.
resource "kubernetes_secret_v1" "maintenance" {
  count = var.enable_dr_exercise ? 1 : 0
  metadata {
    name      = "agentic-maintenance-secrets"
    namespace = kubernetes_namespace_v1.platform.metadata[0].name
    labels    = local.labels
  }
  type = "Opaque"
  data = {
    AGENTIC_DR_ADMIN_URL = local.dr_admin_url
  }
}
