---
title: Node Not Ready
tags: [runbook, kubernetes, nodes]
---

# Node Not Ready Runbook

## Overview
When a node enters `NotReady` state, pods running on that node may be evicted or become unresponsive. This runbook covers diagnosis and remediation.

Severity: **P1** if multiple nodes affected, **P2** for single node
SLA: Acknowledge within 5 minutes

## Symptoms

	- Node shows `NotReady` in `oc get nodes` output
	- Pods on the node are stuck in `Terminating` or `Unknown` state
	- Alerts: `KubeNodeNotReady`, `KubeNodeUnreachable`

## Diagnosis

### 1. Check node status

Run the following to see all nodes and their status

oc get nodes -o wide

Look at the `STATUS` and `AGE` columns. If a node recently went NotReady, check `ROLES` to understand impact (master vs worker).

### 2. Check node conditions

```
oc describe node <node-name>
```

Look at the Conditions section. Common conditions that cause NotReady:
- `MemoryPressure`: True - node is running out of RAM
- `DiskPressure`: True - node disk usage above threshold
- `PIDPressure`: True - too many processes on node
- `NetworkUnavailable`: True - network plugin issue
- `Ready`: False - kubelet can't communicate with API server

### 3. Check kubelet status

SSH into the node (if accessible):

```
ssh core@<node-ip>
sudo systemctl status kubelet
sudo journalctl -u kubelet --since "30 minutes ago" | tail -100
```

**Note**: On OpenShift 4.x nodes, you may need to use `oc debug node/<node-name>` instead:

```
oc debug node/<node-name>
chroot /host
systemctl status kubelet
journalctl -u kubelet --since "30 minutes ago"
```

### 4. Check system resources on the node

```
oc adm top node <node-name>
```

If the node is accessible via debug:
```
chroot /host
df -h
free -m
top -bn1 | head -20
```

## Remediation

**Scenario A - kubelet crashed or hung**

1) Restart kubelet via debug pod:
```
oc debug node/<node-name>
chroot /host
systemctl restart kubelet
```
2) Wait 2-3 minutes for node to rejoin
3) Verify: `oc get node <node-name>`

**Scenario B - Disk Pressure**

1. Identify large files or images consuming disk
2. Clean up unused container images:
```
crictl rmi --prune
```
3. Check for large log files:
```
find /var/log -type f -size +100M
```
4. If persistent, request disk expansion through CMDB ticket

**Scenario C - Network issue**

1. Check if you can ping the node from another node
2. Check OVN/OVS pod status on the affected node:
   ```
   oc get pods -n openshift-ovn-kubernetes -o wide | grep <node-name>
   ```
3. Restart the ovnkube-node pod if its in a bad state:
   ```
   oc delete pod <ovnkube-pod> -n openshift-ovn-kubernetes
   ```
4. If network issue persists, engage the networking team via #net-eng

**Scenario D - Node is completely unreachable**

If the node is a VM:
  - Check the hypervisor / cloud console for the VM status
  - Try rebooting from the cloud console
  - If the VM is gone, the MachineSet should provision a replacement

If bare metal:
  - Engage datacenter team for physical inspection
  - Check IPMI/BMC for hardware errors

## Post-Incident

After the node recovers:
1. Check that all pods are rescheduled: `oc get pods --all-namespaces --field-selector spec.nodeName=<node-name>`
2. Verify no PVCs are stuck: `oc get pvc --all-namespaces | grep -v Bound`
3. Check cluster health: `oc get co` (cluster operators)
4. Document the root cause in the incident channel
