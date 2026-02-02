# Access Requests

## How to request access

All access requests go through ServiceNow. Use the appropriate template:

| Access Type | ServiceNow Template | Approver |
|------------|-------------------|----------|
| OpenShift cluster access | OPENSHIFT-ACCESS | SRE Team Lead |
| Vault secrets access | VAULT-ACCESS | Security Team |
| Quay.io registry | QUAY-ACCESS | SRE Team Lead |
| PagerDuty | PAGERDUTY-ACCESS | SRE Manager |
| Grafana / Prometheus | MONITORING-ACCESS | SRE Team Lead |
| ArgoCD | ARGOCD-ACCESS | SRE Team Lead |
| GitHub Enterprise | GHE-ACCESS | Engineering Manager |

## LDAP Groups

Access is controlled via LDAP groups that sync to OpenShift. Your manager will add you to the appropriate groups.

Key groups:

```
cn=sre-platform,ou=groups,dc=acme,dc=com       → cluster-admin on all clusters
cn=sre-readonly,ou=groups,dc=acme,dc=com        → view access on all clusters
cn=dev-team-<name>,ou=groups,dc=acme,dc=com     → edit access on team namespaces
cn=ci-admins,ou=groups,dc=acme,dc=com           → admin on ci-pipelines namespace
```

Group sync runs every 15 minutes. After your manager adds you, wait up to 15 minutes then verify:

```
oc login https://api.ocp-dev.acme-internal.com:6443
oc auth can-i get pods -n <your-team-namespace>
```

## Service Accounts

For automation and CI/CD, use service accounts instead of personal credentials.

Create a service account:
```
oc create sa <sa-name> -n <namespace>
```

Get a token:
```
oc create token <sa-name> -n <namespace> --duration=8760h
```

**Note**: Long-lived tokens are discouraged. Use short-lived tokens where possible and rotate regularly.

## VPN Access

You need VPN to access the clusters. VPN client: GlobalProtect.

VPN profiles:
- `acme-corp-general` — general corporate access
- `acme-corp-infra` — infrastructure access (includes cluster API endpoints)

Request VPN access via ServiceNow template: VPN-ACCESS
