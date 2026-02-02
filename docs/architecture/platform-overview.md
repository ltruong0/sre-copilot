# Platform Overview

## Introduction

Acme Corp runs its production workloads on a multi-cluster OpenShift platform hosted on IBM Cloud. This document describes the high-level architecture and key components.

## Cluster Topology

We maintain 3 OpenShift clusters:

**Production** (`ocp-prod`)
- Region: us-east
- Version: OpenShift 4.14
- Workers: 12 (8xlarge instances)
- Purpose: Customer-facing applications and APIs

**Staging** (`ocp-staging`)
- Region: us-east  
- Version: OpenShift 4.14
- Workers: 6 (4xlarge instances)
- Purpose: Pre-production testing, performance testing

**Development** (`ocp-dev`)
- Region: us-east
- Version: OpenShift 4.15
- Workers: 4 (2xlarge instances)
- Purpose: Development, feature branches, CI runners

## Namespacing Strategy

Each application team gets a set of namespaces per cluster:

```
<team>-<env>          # main application namespace
<team>-<env>-jobs     # batch jobs and cronjobs
<team>-<env>-config   # configmaps and secrets
```

Example: `payments-prod`, `payments-prod-jobs`, `payments-prod-config`

Network policies restrict traffic between team namespaces. See [Networking](networking.md) for details.

## Key Platform Components

| Component | Technology | Namespace | Purpose |
|-----------|-----------|-----------|---------|
| Ingress | HAProxy (OpenShift Router) | openshift-ingress | External traffic routing |
| Service Mesh | Red Hat OpenShift Service Mesh (Istio) | istio-system | Inter-service communication |
| Monitoring | Prometheus + Grafana | openshift-monitoring | Metrics and dashboards |
| Logging | OpenShift Logging (Loki) | openshift-logging | Centralized logs |
| Certificate Management | cert-manager | cert-manager | TLS certificate lifecycle |
| Secrets | HashiCorp Vault | vault | Secrets management |
| GitOps | ArgoCD | openshift-gitops | Deployment automation |
| CI/CD | Tekton Pipelines | ci-pipelines | Build and test |
| Registry | Quay.io (hosted) | N/A | Container image registry |
| Backup | Velero | velero | Cluster and PV backups |

## Authentication

User authentication flows through IBM Security Verify (OIDC) → OpenShift OAuth → RBAC.

Service accounts use short-lived tokens via the TokenRequest API. Long-lived tokens are deprecated.

LDAP group sync runs every 15 minutes to keep OpenShift groups aligned with the corporate directory. See [Access Requests](../onboarding/access-requests.md) for how groups map to cluster roles.

## Resource Quotas

Each team namespace has default quotas:

```yaml
apiVersion: v1
kind: ResourceQuota
metadata:
  name: default-quota
spec:
  hard:
    requests.cpu: "8"
    requests.memory: 16Gi
    limits.cpu: "16"
    limits.memory: 32Gi
    pods: "50"
    persistentvolumeclaims: "10"
```

Teams can request quota increases via ServiceNow ticket (template: QUOTA-INCREASE).
