# Terraform

One module, four environments. The module provisions the substrate the
workloads need before they can start:

* the namespace, with restricted Pod Security, a resource quota and a limit range
* the four database roles, with the privilege split the isolation model depends
  on: `agentic_app` may never bypass row level security, `agentic_provisioner`
  is the only role that may and it is `NOLOGIN`, and `agentic_maintenance` — the
  only role that can create a database — exists solely for the restore exercise
* the Kubernetes secrets that carry connection strings and keys, split so that
  the API and the worker never mount the maintenance identity

Workloads themselves are Kubernetes manifests (`../kubernetes`), applied by the
pipeline after this module converges. Keeping them apart means a workload
rollback never rolls back a database role.

```
cd environments/staging
terraform init -backend-config=backend.hcl   # backend.hcl is not committed
terraform plan
terraform apply
```

## Secrets

Every sensitive variable is declared `sensitive` and has no default. Supply
them as `TF_VAR_*` from the pipeline's secret store. **Do not put production
credentials in this repository, in a committed `.tfvars`, or in a variable
default.** State will contain them, so the backend must be encrypted and
access-controlled; that is why `backend "s3" {}` is left to be configured per
deployment rather than hard-coded here.

## Environment differences

| Environment | Policy mode | Secret backend | KMS       | DR exercise |
|-------------|-------------|----------------|-----------|-------------|
| dev         | monitor     | env            | local     | disabled    |
| test        | enforce     | env            | local     | enabled     |
| staging     | enforce     | vault          | local     | enabled     |
| production  | enforce     | vault          | aws-kms   | enabled     |

`enable_dr_exercise = false` in dev is deliberate: without the maintenance
identity the restore exercise refuses to run and control DRP-001 stays
NOT_EVIDENCED there, rather than reporting a recovery objective nobody proved.

## Verification status

This configuration has **not** been applied. No cloud account, cluster or
Terraform binary was available while it was written, so it has not been through
`terraform init`, `validate`, `plan` or `apply`. It is reviewed HCL, not proven
infrastructure. `docs/assurance/FINAL_GAP_AUDIT.md` records the same.
