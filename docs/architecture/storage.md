# Storage

## Storage Classes

We have the following storage classes available on our clusters:

| Storage Class | Provisioner | Type | Reclaim Policy | Use Case |
|:---:|:---:|:---:|:---:|:---:|
| ibmc-vpc-block-10iops | IBM VPC | Block | Delete | General purpose |
| ibmc-vpc-block-custom | IBM VPC | Block | Delete | High performance |
| ocs-storagecluster-cephfs | ODF (CephFS) | File | Delete | Shared storage (RWX) |
| ocs-storagecluster-ceph-rbd | ODF (Ceph RBD) | Block | Delete | Databases |

Default storage class is `ibmc-vpc-block-10iops`.

## Choosing a storage class

<div class="admonition tip">
<p>Use this decision tree:</p>
<ul>
<li>Need shared access (RWX)? → Use CephFS</li>
<li>Running a database? → Use Ceph RBD</li>
<li>Need high IOPS? → Use ibmc-vpc-block-custom</li>
<li>Everything else → Use ibmc-vpc-block-10iops (default)</li>
</ul>
</div>

## PVC Best Practices

1. Always set resource requests in PVC specs
2. Use `volumeMode: Filesystem` unless your app specifically needs raw block
3. For databases, always use `ReadWriteOnce` (RWO)
4. Don't over-provision - start small and resize later

Example PVC:

```
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: app-data
spec:
  accessModes:
    - ReadWriteOnce
  storageClassName: ibmc-vpc-block-10iops
  resources:
    requests:
      storage: 20Gi
```

## Volume Expansion

Our storage classes support volume expansion. To resize a PVC:

```
oc patch pvc <pvc-name> -n <namespace> -p '{"spec":{"resources":{"requests":{"storage":"50Gi"}}}}'
```

**Note**: For block storage the pod must be restarted after the volume is resized. For CephFS, the resize is online.

## Backup and Recovery

Velero handles PV backups. Backups run on a schedule:
- Production: every 6 hours, retain 30 days
- Staging: daily, retain 7 days
- Dev: weekly, retain 3 days

To restore a specific PVC from backup, see the Velero restore docs or contact the SRE team.

## Troubleshooting

For PVC-related issues see [PVC Issues guide](../troubleshooting/pvc-issues.md)
