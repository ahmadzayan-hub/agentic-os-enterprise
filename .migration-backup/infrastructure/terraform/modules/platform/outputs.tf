output "namespace" {
  value = kubernetes_namespace_v1.platform.metadata[0].name
}

output "application_secret_name" {
  value = kubernetes_secret_v1.application.metadata[0].name
}

output "dr_exercise_enabled" {
  value       = var.enable_dr_exercise
  description = "When false, control DRP-001 cannot be evidenced in this environment."
}
