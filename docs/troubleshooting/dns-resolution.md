# DNS Resolution Issues

## Overview

DNS problems manifest as application failures connecting to other services, external APIs or databases. this guide covers common DNS issues on our OpenShift clusters.

## Quick Check

Run a DNS lookup from inside a pod:

```
oc exec <any-pod> -n <namespace> -- nslookup kubernetes.default.svc.cluster.local
```

If this fails, DNS is broken at the cluster level.

## Common issues

### CoreDNS pods not running

```
oc get pods -n openshift-dns
```

All dns-default pods should be Running and Ready. If any are crashing, check their logs:

```
oc logs <dns-pod> -n openshift-dns
```

### DNS timeout / slow resolution

If DNS works but is slow:

1. Check CoreDNS metrics in Grafana (dashboard: "CoreDNS")
2. Look for high `coredns_dns_request_duration_seconds` values
3. Check if CoreDNS pods are CPU throttled:
   ```
   oc adm top pods -n openshift-dns
   ```

### Service not resolvable

If a specific service can't be resolved:

```
oc exec <pod> -- nslookup <service-name>.<namespace>.svc.cluster.local
```

If this fails, check:
- Service exists: `oc get svc <service-name> -n <namespace>`
- Service has endpoints: `oc get endpoints <service-name> -n <namespace>`
- Endpoints are not empty (pods must be ready)

### External domain not resolvable

If pods can't resolve external domains (like api.stripe.com):

1. Check upstream DNS configuration:
```
oc get dns.operator/default -o yaml
```

2. Check if the node can resolve:
```
oc debug node/<node-name> -- chroot /host nslookup api.stripe.com
```

3. Check NetworkPolicy - egress to DNS (port 53) must be allowed

### ndots issue

Kubernetes default ndots is 5, which means short names get the search domains appended first. This can cause:
- Extra DNS queries for every lookup
- Slow resolution for external domains

**Fix**: Use FQDNs in application configs (with trailing dot):
```
# Instead of: api.stripe.com
# Use: api.stripe.com.
```

Or reduce ndots in the pod spec:
```yaml
spec:
  dnsConfig:
    options:
    - name: ndots
      value: "2"
```

## Escalation

if DNS issues affect the entire cluster, escalate immediately to the platform team via PagerDuty (service: platform-sre).
