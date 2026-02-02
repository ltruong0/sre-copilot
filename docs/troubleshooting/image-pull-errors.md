---
title: Image Pull Errors
category: troubleshooting
---

# Troubleshooting: Image Pull Errors

When pods fail to start with `ImagePullBackOff` or `ErrImagePull`, use this guide.

## Symptoms

```
$ oc get pods -n myapp-prod
NAME                     READY   STATUS             RESTARTS   AGE
myapp-7b8f9d4c5-x2k9j   0/1     ImagePullBackOff   0          5m
```

## Diagnosis steps

### 1. Get the full error message

```
oc describe pod <pod-name> -n <namespace> | grep -A5 "Events"
```

Common errors:
- `unauthorized: authentication required` → registry auth issue
- `manifest unknown` → image tag doesn't exist  
- `connection refused` → can't reach the registry
- `x509: certificate signed by unknown authority` → TLS issue

### 2. Check the image reference

```
oc get pod <pod-name> -n <namespace> -o jsonpath='{.spec.containers[*].image}'
```

Verify:
   - The image name is spelled correctly
   - The tag exists in the registry
   - The registry URL is correct (quay.io not docker.io)

### 3. Registry authentication

Check if the namespace has a pull secret:

```
oc get secret -n <namespace> | grep pull
```

For Quay.io images, the pull secret should be linked to the service account:

```
oc get sa default -n <namespace> -o yaml | grep -A5 imagePullSecrets
```

If the pull secret is missing or expired:

```bash
# Create a new pull secret
oc create secret docker-registry quay-pull \
  --docker-server=quay.io \
  --docker-username=acme+ci-bot \
  --docker-password=<token> \
  -n <namespace>

# Link it to the default service account
oc secrets link default quay-pull --for=pull -n <namespace>
```

### 4. Network connectivity

If the error is `connection refused` or timeout, check if the node can reach the registry:

```
oc debug node/<node-name> -- chroot /host curl -I https://quay.io/v2/
```

Check EgressNetworkPolicy if one exists:
```
oc get egressnetworkpolicy -n <namespace> -o yaml
```

The egress policy must allow traffic to `quay.io` on port 443.

### 5. TLS certificate issues

If you see x509 errors, the node may be missing the CA certificate for internal registries.

Check the node's trust bundle:
```
oc debug node/<node-name> -- chroot /host trust list | grep -i acme
```

For internal registries, add the CA to the cluster-wide proxy config or the image.config.openshift.io resource.

## Quick fixes

| Problem | Fix |
|---------|-----|
| Wrong image tag | Fix the tag in the deployment manifest |
| Expired pull secret | Regenerate and recreate the secret |
| Missing pull secret | Create secret and link to SA |
| Registry unreachable | Check egress policies and DNS |
| Internal registry TLS | Add CA cert to cluster trust bundle |
