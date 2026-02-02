---
title: Pod Troubleshooting Guide
category: runbook
tags: kubernetes, troubleshooting, pods
---

# Pod Troubleshooting Guide

This runbook covers common pod issues in OpenShift/Kubernetes clusters.

## CrashLoopBackOff

A pod in CrashLoopBackOff state indicates that the container is repeatedly crashing.

### Common Causes

1. Application errors
2. Missing dependencies
3. Configuration issues
4. Resource constraints

### Troubleshooting Steps

1. Check pod logs:

```bash
oc logs <pod-name> -n <namespace>
```

2. Check previous container logs:

```bash
oc logs <pod-name> -n <namespace> --previous
```

3. Describe the pod for events:

```bash
oc describe pod <pod-name> -n <namespace>
```

## ImagePullBackOff

This error occurs when Kubernetes cannot pull the container image.

### Common Causes

- Incorrect image name or tag
- Registry authentication issues
- Network connectivity problems
- Image doesn't exist

### Resolution

1. Verify image name and tag
2. Check image pull secrets
3. Test registry connectivity

## OOMKilled

The container was terminated because it exceeded memory limits.

### Solution

Increase memory limits in the deployment:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: my-app
spec:
  template:
    spec:
      containers:
      - name: app
        resources:
          limits:
            memory: "512Mi"
          requests:
            memory: "256Mi"
```
