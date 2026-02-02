---
title: certificate renewal
author: jsmith
---

# Certificate Renewal

Procedures for renewing TLS certificates across the platform.

### When to use this runbook

Use when:
- cert-manager alerts fire for expiring certificates
- Manual certificates (non-cert-manager) are approaching expiry
- Wildcard certificate needs renewal

## Checking certificate expiry

### Cert-Manager managed certificates

List all certificates and their status:

```
oc get certificates --all-namespaces
```

Check specific certificate details:

```
oc describe certificate <cert-name> -n <namespace>
```

The `READY` column should be `True`. If `False`, check the certificate's events for errors.

### Non cert-manager certificates (manual)

For certificates stored as secrets directly:

```
oc get secret <secret-name> -n <namespace> -o jsonpath='{.data.tls\.crt}' | base64 -d | openssl x509 -noout -dates
```

We have a script that checks all manual certs across namespaces. Its located at:

    /opt/scripts/check-cert-expiry.sh

Run it with:

    ./check-cert-expiry.sh --warn-days 30

## Renewal Procedures

### Procedure A: cert-manager auto-renewal failed

cert-manager should auto-renew certificates 30 days before expiry. If it failed:

1. Check the cert-manager pods are running
```
oc get pods -n cert-manager
```

2. Check cert-manager logs
```
oc logs -l app=cert-manager -n cert-manager --tail=200
```

3. Common issues:
   - DNS01 challenge failing - check cloud DNS credentials
   - HTTP01 challenge failing - check ingress is routing .well-known/acme-challenge
   - Rate limited by Let's Encrypt - wait and retry, or use staging endpoint
   - ClusterIssuer misconfigured - check `oc get clusterissuer -o yaml`

4. Force re-issue by deleting the certificate secret:
```
oc delete secret <cert-secret-name> -n <namespace>
```
cert-manager will detect the missing secret and re-issue

### Procedure B: Wildcard cert renewal

Our wildcard cert (*.acme-internal.com) is managed outside cert-manager.

<b>This is a manual process requiring approval from the security team.</b>

Steps:
1. Generate a new CSR:
```
openssl req -new -newkey rsa:2048 -nodes -keyout wildcard.key -out wildcard.csr -subj "/CN=*.acme-internal.com/O=Acme Corp"
```

2. Submit CSR to the CA team via ServiceNow ticket (template: CERT-RENEWAL)
3. Once you receive the signed cert, create the new secret:
```
oc create secret tls wildcard-cert --cert=wildcard.crt --key=wildcard.key -n openshift-ingress --dry-run=client -o yaml | oc apply -f -
```

4. Restart the router pods to pick up the new cert:
```
oc rollout restart deployment/router-default -n openshift-ingress
```

5. verify the new cert is being served:
```
echo | openssl s_client -connect apps.acme-internal.com:443 -servername apps.acme-internal.com 2>/dev/null | openssl x509 -noout -dates
```

### Procedure C: Application-specific certificates

Some apps manage their own certs via vault or configmaps.

check the app's README or deployment docs for cert renewal procedures. Common locations:
- Java apps: keystore in a mounted secret
- Node apps: cert files in configmap
- Python apps: environment variables pointing to cert paths

## Monitoring

We monitor cert expiry via Prometheus. The relevant metrics:

- `certmanager_certificate_expiration_timestamp_seconds` - for cert-manager certs
- `probe_ssl_earliest_cert_expiry` - for blackbox exporter probes

Alert rules are defined in the `sre-monitoring` namespace, configmap `prometheus-rules`.
