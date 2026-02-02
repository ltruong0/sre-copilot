---
title: Pod CrashLoopBackOff
tags: [runbook, kubernetes, pods]
last_updated: 2025-08-20
---

# Runbook: Pod CrashLoopBackOff

### Overview

This runbook covers the procedure for diagnosing and resolving pods stuck in `CrashLoopBackOff` state. This is one of the most common issues we see in production.

**Severity**: P2 (P1 if affecting customer-facing services)
<br/>
**SLA**: Acknowledge within 15 minutes, resolve within 2 hours

## Symptoms

- Pod status shows `CrashLoopBackOff` in `oc get pods` output
- Container keeps restarting with increasing backoff delay
- Application logs may show errors before container exits
- Alerts firing: `PodCrashLooping` in Prometheus/AlertManager

## Step 1: Identify the affected pod

First get the pod status and check which container is crashing:

```
oc get pods -n <namespace> | grep CrashLoop
```

Then describe the pod for more details:

```
oc describe pod <pod-name> -n <namespace>
```

Look for the `Last State` section - it will tell you the exit code:
- Exit code 1: Application error
- Exit code 137: OOMKilled (out of memory)  
- Exit code 139: Segfault
- Exit code 143: SIGTERM (graceful shutdown failed)

## Step 2 - Check the logs

Get the current container logs:

```bash
oc logs <pod-name> -n <namespace>
```

If the container already restarted, check previous container logs:

```bash
oc logs <pod-name> -n <namespace> --previous
```

<div class="admonition warning">
<p class="admonition-title">Warning</p>
<p>If the pod has multiple containers, you need to specify the container name with -c flag</p>
</div>

### Step 3: Common causes and fixes

#### OOMKilled (Exit Code 137)

The container exceeded its memory limit. Check current limits:

```
oc get pod <pod-name> -n <namespace> -o jsonpath='{.spec.containers[*].resources}'
```

To fix, either:
1. increase the memory limit in the deployment
2. investigate the application for memory leaks
3. check if the JVM heap size is configured correctly (for Java apps)

Example fix:
```yaml
resources:
  limits:
    memory: "1Gi"    # was 512Mi
  requests:
    memory: "512Mi"
```

Apply with:
```
oc apply -f deployment.yaml -n <namespace>
```

#### Application Configuration Error

- Check ConfigMaps: `oc get configmap -n <namespace>`
- Check Secrets: `oc get secrets -n <namespace>`
- Verify environment variables: `oc set env pod/<pod-name> --list -n <namespace>`

#### Image Issues

check that the image exists and is pullable:

```
oc get pod <pod-name> -n <namespace> -o jsonpath='{.spec.containers[0].image}'
```

Verify the image tag exists in the registry. see [Image Pull Errors](../troubleshooting/image-pull-errors.md) for more details

##### Liveness/Readiness probe failures

Sometimes the probes are misconfigured. check the probe settings:

```
oc get pod <pod-name> -o yaml | grep -A 10 livenessProbe
```

Common fixes:
* Increase `initialDelaySeconds` if the app takes a while to start
* Increase `timeoutSeconds` if the health endpoint is slow
* Make sure the health endpoint path is correct

## Step 4: Escalation

If the above steps don't resolve the issue:

1. Check if this is a known issue in the #sre-platform channel
2. Check the deployment history: `oc rollout history deployment/<deployment-name> -n <namespace>`
3. Consider rolling back: `oc rollout undo deployment/<deployment-name> -n <namespace>`
4. If still unresolved, escalate to the application team via PagerDuty

## Related

- [High Memory Usage Runbook](high-memory-usage.md)
- [Image Pull Errors](../troubleshooting/image-pull-errors.md)
- <a href="https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/">Kubernetes Pod Lifecycle</a>
