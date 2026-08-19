# production environment.
#
# State lives in an encrypted remote backend because the platform module holds
# database passwords and the signing key in state. Configure it with
# `terraform init -backend-config=backend.hcl`; the backend config is not
# committed because it names infrastructure that differs per tenant of this
# platform.

terraform {
  required_version = ">= 1.6.0"
  backend "s3" {}

  # The root module must name the same provider sources as the module it calls.
  # Without this, a bare `provider "postgresql"` block below resolves to the
  # implicit hashicorp/postgresql, which does not exist, and `terraform init`
  # fails with "Missing required provider".
  required_providers {
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = ">= 2.30"
    }
    postgresql = {
      source  = "cyrilgdn/postgresql"
      version = ">= 1.22"
    }
  }
}

provider "kubernetes" {
  config_path    = var.kubeconfig_path
  config_context = var.kube_context
}

provider "postgresql" {
  host      = var.database_host
  port      = var.database_port
  database  = var.database_name
  username  = var.database_superuser
  password  = var.database_superuser_password
  sslmode   = "require"
  superuser = false
}

module "platform" {
  source = "../../modules/platform"

  environment   = "production"
  namespace     = "agentic-os-production"
  database_host = var.database_host
  database_port = var.database_port
  database_name = var.database_name

  database_owner_password       = var.database_owner_password
  database_app_password         = var.database_app_password
  database_maintenance_password = var.database_maintenance_password
  jwt_secret                    = var.jwt_secret
  kms_local_key                 = var.kms_local_key

  kms_backend        = "aws-kms"
  secret_backend     = "vault"
  policy_mode        = "enforce"
  enable_dr_exercise = true

  resource_quota = {
    cpu    = "32"
    memory = "64Gi"
    pods   = "100"
  }
}

output "namespace" {
  value = module.platform.namespace
}
