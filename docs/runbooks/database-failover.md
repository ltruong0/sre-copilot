# Database Failover Procedure

**Last updated**: March 2025

This runbook covers failover procedures for our PostgreSQL clusters running on OpenShift via the CloudNativePG operator.

## Architecture

We run 3 PostgreSQL clusters:
- `pg-primary` in `database-prod` namespace - main application database
- `pg-analytics` in `analytics-prod` namespace - analytics/reporting
- `pg-auth` in `auth-prod` namespace - authentication service

Each cluster runs 3 replicas (1 primary + 2 standby) with synchronous replication.

## When to failover

Failover should be triggered when:
* Primary pod is unresponsive for more than 5 minutes
* Primary node has hardware failure
* Planned maintenance on the primary node
* Database corruption detected on primary

**DO NOT failover for:**
* Temporary network blips (< 2 minutes)
* High CPU/memory that is being addressed
* Application-layer issues not related to the database

## Pre-Failover checklist

Before initiating failover:

- [ ] Confirm the issue is with the database primary, not the application
- [ ] Check replication lag: `SELECT pg_last_wal_receive_lsn() - pg_last_wal_replay_lsn();`
- [ ] Notify the team in #dba-team and #sre-platform
- [ ] If planned maintenance, notify application teams 30 minutes in advance
- [ ] Ensure at least one standby is fully synced

## Automatic Failover

CloudNativePG handles automatic failover. Check if it already happened:

```bash
oc get cluster <cluster-name> -n <namespace> -o yaml | grep -A5 currentPrimary
```

If auto-failover occurred, verify the new primary is healthy:

```
oc exec -it <new-primary-pod> -n <namespace> -- psql -U postgres -c "SELECT pg_is_in_recovery();"
```

Result should be `f` (false) for the primary.

## Manual Failover

If auto-failover didn't trigger or you need a planned failover:

### Step 1 - Identify the target standby

```bash
oc get pods -n <namespace> -l role=replica
```

Check which standby has the least replication lag:

```
for pod in $(oc get pods -n <namespace> -l role=replica -o name); do
  echo "=== $pod ==="
  oc exec $pod -n <namespace> -- psql -U postgres -c "SELECT now() - pg_last_xact_replay_timestamp() AS replication_lag;"
done
```

### Step 2 - promote the standby

Using the CloudNativePG plugin:

```bash
oc cnpg promote <cluster-name> <target-pod> -n <namespace>
```

Or by patching the cluster resource:

```yaml
oc patch cluster <cluster-name> -n <namespace> --type merge -p '{"spec":{"primaryUpdateStrategy":"unsupervised","primaryUpdateMethod":"switchover","targetPrimary":"<target-pod>"}}'
```

### Step 3 - Verify

1. Check the new primary: `oc get cluster <cluster-name> -n <namespace>`
2. Verify applications can connect
3. Check replication is working to remaining standbys

```sql
SELECT client_addr, state, sent_lsn, write_lsn, flush_lsn, replay_lsn 
FROM pg_stat_replication;
```

## Connection String Update

Our apps use a Kubernetes service that points to the primary. CloudNativePG updates this automatically.

Service name pattern: `<cluster-name>-rw` for read-write, `<cluster-name>-ro` for read-only.

Verify the service is pointing to the new primary:
```
oc get endpoints <cluster-name>-rw -n <namespace>
```

If using pgBouncer, restart it:
```
oc rollout restart deployment/<cluster-name>-pgbouncer -n <namespace>
```

## Rollback

If the failover caused issues and you need to go back:

1. **Do not** try to promote the old primary directly
2. Let CloudNativePG re-sync the old primary as a standby first
3. Wait for full sync, then failover again to the original primary
4. Monitor replication lag during this process

## Post-Failover

After a successful failover:
1. Update the incident ticket with timeline and actions taken
2. Investigate root cause of the original failure
3. Ensure backup schedule is running against the new primary:
   `oc get scheduledbackup -n <namespace>`
4. Verify monitoring dashboards show the new topology
