# Kubernetes manifests

`base/` carries the workload definition; `overlays/{dev,test,staging,production}`
carry the differences that are genuinely environmental — the environment name,
whether policy is enforced or merely monitored, and where secrets come from.

```
kubectl apply -k infrastructure/kubernetes/overlays/staging
```

## What is deliberately not here

* **Secrets.** The workloads read a `Secret` named `agentic-secrets`; nothing in
  this repository creates it. Terraform wires it to the environment's secret
  manager. The disaster recovery CronJob additionally reads
  `agentic-maintenance-secrets`, which holds the only identity permitted to
  create a scratch database — no other workload can mount it.
* **The database.** These manifests assume a managed PostgreSQL 16 with
  pgvector reachable from the `data` namespace. The cluster bootstrap in
  `database/bootstrap/` must have been applied by a superuser before the
  migration Job runs; `agentic_owner` cannot create roles or extensions itself.
* **The image registry.** Image references use a placeholder registry. Set the
  real one with a kustomize `images:` entry per environment.

## Verification status

These manifests parse as YAML and follow the structure the workloads expect.
They have **not** been applied to a cluster from this repository: no cluster
was reachable during the build, and `kubectl`/`kustomize` are not installed in
that environment. Treat them as reviewed-but-unexecuted until a first apply
into dev is recorded. `docs/assurance/FINAL_GAP_AUDIT.md` says the same.
