---
title: high memory
tags: runbook
---

# High Memory Usage

How to investigate and resolve high memory usage on pods and nodes.

## Alerts

The following alerts may trigger for memory issues:

| Alert Name | Threshold | Severity |
|-----------|-----------|----------|
| ContainerMemoryUsageHigh | >85% of limit | warning |
| ContainerOOMKilled | container OOM killed | critical |
| NodeMemoryPressure | node memory > 90% | critical |

## Pod-level memory investigation

### Check current memory usage

```
oc adm top pods -n <namespace> --sort-by=memory
```

For a specific pod:

```
oc adm top pod <pod-name> -n <namespace>
```

### Check memory limits

```
oc get pod <pod-name> -n <namespace> -o jsonpath='{range .spec.containers[*]}{.name}{"\t"}{.resources.limits.memory}{"\t"}{.resources.requests.memory}{"\n"}{end}'
```

### Check for OOMKilled events

```
oc get events -n <namespace> --field-selector reason=OOMKilling --sort-by=.lastTimestamp
```

Also check pod status:
```
oc get pod <pod-name> -n <namespace> -o jsonpath='{.status.containerStatuses[*].lastState}'
```

## Common causes

### 1. Memory leak in application

Signs:
 - Memory usage steadily increases over time
 - Restarting the pod temporarily fixes the issue
 - Usage pattern doesn't correlate with traffic

Investigation:
- Check Grafana dashboard for the app's memory usage over time
- Get a heap dump if the application supports it (Java: jmap, Node: --inspect)
- Review recent code changes that might have introduced the leak

### 2. Undersized memory limits

Signs:
 - Pod gets OOMKilled shortly after startup or during peak traffic
 - Memory usage quickly reaches the limit

Fix:
- Review actual memory needs in Grafana (look at P95 over last 7 days)
- Increase memory limit with some headroom:

```
oc set resources deployment/<name> -n <namespace> --limits=memory=2Gi --requests=memory=1Gi
```

### 3. JVM heap misconfiguration

For Java applications, the JVM heap must be configured to fit within the container memory limit.

Rule of thumb: JVM heap should be ~75% of container memory limit.

check current JVM settings:
```
oc exec <pod-name> -n <namespace> -- jcmd 1 VM.flags | grep -i heap
```

Common env vars to set:
```
JAVA_OPTS="-Xmx768m -Xms512m"
# or for newer JVMs:
JAVA_OPTS="-XX:MaxRAMPercentage=75.0"
```

### 4. Too many replicas on one node

If a node is under memory pressure:
```
oc describe node <node-name> | grep -A20 "Allocated resources"
```

Consider:
- Adding pod anti-affinity rules
- Setting appropriate requests so scheduler distributes pods better
- Scaling the node pool

## Node-level memory investigation

```
oc adm top nodes
```

For detailed breakdown:

```
oc debug node/<node-name> -- chroot /host free -m
```

Check what's consuming memory on the node:

```
oc debug node/<node-name> -- chroot /host bash -c "ps aux --sort=-%mem | head -20"
```

## Escalation

If memory issues persist after investigation:
1. For application memory leaks -> escalate to the application development team
2. For cluster-wide memory pressure -> engage capacity planning (#sre-capacity)
3. For suspected infrastructure issue -> check with cloud provider support
