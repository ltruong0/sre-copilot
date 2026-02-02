# PVC Issues

Common problems with PersistentVolumeClaims and how to fix them.

## PVC stuck in Pending

```
oc get pvc -n <namespace>
```

If a PVC is stuck in `Pending`:

### Check events
```
oc describe pvc <pvc-name> -n <namespace>
```

### Common causes

**No matching StorageClass**
- Verify the storage class exists: `oc get sc`
- Check for typos in the PVC's `storageClassName`

**Insufficient capacity**
- The cloud provider may be out of capacity in the availability zone
- Try a different storage class or zone

**Volume limit reached**
- IBM Cloud VPC has limits on volumes per node
- Check: `oc describe node <node-name> | grep -i attachable`

## PVC stuck in Terminating

This usually means a pod is still using the PVC.

1. Find pods using the PVC:
```
oc get pods -n <namespace> -o json | jq -r '.items[] | select(.spec.volumes[]?.persistentVolumeClaim.claimName == "<pvc-name>") | .metadata.name'
```

2. Delete or update the pod first, then the PVC will terminate

3. If no pods are using it and it's still stuck, check for finalizers:
```
oc get pvc <pvc-name> -n <namespace> -o jsonpath='{.metadata.finalizers}'
```

Remove finalizers (last resort):
```
oc patch pvc <pvc-name> -n <namespace> -p '{"metadata":{"finalizers":null}}'
```

## Data recovery from PVC

If a PVC was accidentally deleted:

1. check Velero backups: `velero backup get`
2. Restore the PVC:
```
velero restore create --from-backup <backup-name> --include-resources pvc --include-namespaces <namespace> --selector app=<app-label>
```

## Volume mount permission errors

If pods fail with permission denied when writing to a volume:

check the pod's security context:
```
oc get pod <pod> -o yaml | grep -A10 securityContext
```

Common fix — set fsGroup to match the container's user:
```yaml
spec:
  securityContext:
    fsGroup: 1000
```
