---
title: Ingress 502 Bad Gateway
tags:
 - troubleshooting
 - networking
 - ingress
---

# Ingress 502 Bad Gateway Errors

## What is a 502?

A 502 Bad Gateway means the OpenShift router (HAProxy) received an invalid response from the upstream pod, or couldn't connect to it at all. The problem is almost always on the backend, not the router.

## Diagnosis

### Step 1: Identify the route

```
oc get route -n <namespace> <route-name> -o yaml
```

Note the `spec.to.name` — this is the target service.

### Step 2: Check the backend service and pods

```bash
oc get svc <service-name> -n <namespace>
oc get endpoints <service-name> -n <namespace>
oc get pods -l <selector> -n <namespace>
```

**If endpoints are empty**: no pods are matching the service selector, or pods aren't ready.

### Step 3: check pod readiness

```
oc get pods -n <namespace> -o wide
```

If pods show `0/1 Ready`, the readiness probe is failing. Check:
```
oc describe pod <pod-name> -n <namespace> | grep -A15 "Readiness"
```

### Step 4: test connectivity from router to pod

```bash
# Get a router pod
ROUTER_POD=$(oc get pods -n openshift-ingress -l ingresscontroller.operator.openshift.io/deployment-ingresscontroller=default -o name | head -1)

# Test connectivity to the backend
oc exec $ROUTER_POD -n openshift-ingress -- curl -sI http://<pod-ip>:<port>/health
```

## Common causes and fixes

### Application not listening on the right port

The route/service port must match what the application is actually listening on.

Check what the container is listening on:
```
oc exec <pod> -n <namespace> -- ss -tlnp
```

Compare with the service definition:
```
oc get svc <svc> -n <namespace> -o yaml
```

### TLS misconfiguration

If the route has `tls.termination: reencrypt` or `passthrough`, the backend must serve TLS.

For `edge` termination (most common), the router handles TLS and talks HTTP to the backend.

Check route TLS settings:
```
oc get route <route-name> -n <namespace> -o jsonpath='{.spec.tls.termination}'
```

### Pod taking too long to respond

HAProxy has a default timeout. If the backend is slow:

Add an annotation to the route:
```
oc annotate route <route-name> -n <namespace> haproxy.router.openshift.io/timeout=120s
```

### Too many connections

If the backend can't handle the connection volume:

- Scale up replicas: `oc scale deployment/<name> --replicas=5 -n <namespace>`
- Check if HPA is configured and working:
  ```
  oc get hpa -n <namespace>
  ```

## Quick Checklist

- [ ] Pods are Running and Ready
- [ ] Service endpoints are populated
- [ ] Service port matches application port
- [ ] Route TLS termination matches backend capability
- [ ] Application health endpoint returns 200
- [ ] No NetworkPolicy blocking router → pod traffic
- [ ] HAProxy timeout is sufficient for the application
