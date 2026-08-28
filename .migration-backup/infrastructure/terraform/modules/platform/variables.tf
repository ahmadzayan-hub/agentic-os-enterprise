variable "environment" {
  type        = string
  description = "dev, test, staging or production."
  validation {
    condition     = contains(["dev", "test", "staging", "production"], var.environment)
    error_message = "environment must be one of dev, test, staging, production."
  }
}

variable "namespace" {
  type        = string
  description = "Kubernetes namespace for the platform workloads."
}

variable "database_host" {
  type        = string
  description = "Hostname of the managed PostgreSQL 16 instance with pgvector."
}

variable "database_port" {
  type    = number
  default = 5432
}

variable "database_name" {
  type    = string
  default = "agentic"
}

# ---------------------------------------------------------------------------
# Secrets. Every one of these is marked sensitive and none has a default: the
# caller supplies them from the environment's secret manager (a data source, a
# Vault provider read, a TF_VAR from the pipeline's secret store). Terraform
# state must be stored in an encrypted backend because it will contain them.
# Do not put production credentials in this repository or in a .tfvars file
# that is committed.
# ---------------------------------------------------------------------------
variable "database_owner_password" {
  type      = string
  sensitive = true
}

variable "database_app_password" {
  type      = string
  sensitive = true
}

variable "database_maintenance_password" {
  type        = string
  sensitive   = true
  description = "Break-glass identity used only by the disaster recovery exercise."
}

variable "jwt_secret" {
  type      = string
  sensitive = true
}

variable "kms_local_key" {
  type        = string
  sensitive   = true
  description = "Base64 32-byte data key. Empty when kms_backend is a managed KMS."
}

variable "kms_backend" {
  type    = string
  default = "local"
}

variable "secret_backend" {
  type    = string
  default = "vault"
}

variable "policy_mode" {
  type        = string
  default     = "enforce"
  description = "enforce blocks on a policy denial; monitor records it and continues."
  validation {
    condition     = contains(["enforce", "monitor"], var.policy_mode)
    error_message = "policy_mode must be enforce or monitor."
  }
}

variable "enable_dr_exercise" {
  type        = bool
  default     = true
  description = <<-EOT
    Whether to provision the maintenance secret the disaster recovery exercise
    needs. When false the exercise refuses to run and control DRP-001 stays
    NOT_EVIDENCED, which is the honest outcome for an environment that has
    never proved a restore.
  EOT
}

variable "resource_quota" {
  type = object({
    cpu    = string
    memory = string
    pods   = string
  })
  default = {
    cpu    = "16"
    memory = "32Gi"
    pods   = "50"
  }
}
