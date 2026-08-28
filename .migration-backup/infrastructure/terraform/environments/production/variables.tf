variable "kubeconfig_path" {
  type    = string
  default = "~/.kube/config"
}

variable "kube_context" {
  type = string
}

variable "database_host" { type = string }
variable "database_port" {
  type    = number
  default = 5432
}
variable "database_name" {
  type    = string
  default = "agentic"
}

# Supplied by the pipeline from the secret manager as TF_VAR_* values. There
# are no defaults and nothing sensitive is committed: a .tfvars file holding
# any of these must never enter version control.
variable "database_superuser" { type = string }
variable "database_superuser_password" {
  type      = string
  sensitive = true
}
variable "database_owner_password" {
  type      = string
  sensitive = true
}
variable "database_app_password" {
  type      = string
  sensitive = true
}
variable "database_maintenance_password" {
  type      = string
  sensitive = true
}
variable "jwt_secret" {
  type      = string
  sensitive = true
}
variable "kms_local_key" {
  type      = string
  sensitive = true
  default   = ""
}
