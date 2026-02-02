---
title: change management policy
status: approved
last_review: 2025-06-01
---

# Change Management

All changes to production systems must follow this process.

## Change Categories

**Standard Change** — Pre-approved, low risk, follows a documented procedure.
Examples: scaling replicas, updating a configmap, deploying a new version via GitOps.
Approval: None required (but must be logged)

**Normal Change** — Requires review and approval before implementation.
Examples: new service deployment, infrastructure changes, network policy updates, storage class changes.
Approval: SRE Team Lead + affected application team lead

**Emergency Change** — Bypasses normal approval for urgent fixes during incidents.
Examples: hotfix during P1 incident, emergency certificate renewal.
Approval: Incident Commander can approve. Post-approval from SRE Manager within 24 hours.

## Change Request Process

### Standard Changes

1. Create a PR in the GitOps repo
2. PR gets auto-merged after CI passes (for approved standard change patterns)
3. ArgoCD auto-syncs
4. Log the change in #changes Slack channel

### Normal Changes

1. Create a Change Request in ServiceNow (template: CHANGE-REQUEST)
2. Include:
   - Description of the change
   - Impact assessment
   - Rollback plan
   - Testing evidence (staging results)
   - Maintenance window (if needed)
3. Get approvals
4. Implement during the agreed maintenance window
5. Verify and close the CR

### Emergency Changes

1. Implement the fix immediately
2. Create a retrospective Change Request in ServiceNow within 24 hours
3. Get post-approval from SRE Manager
4. Include the change in the post-mortem documentation

## Maintenance Windows

Regular maintenance windows:
- **Tuesday 10pm - 2am ET**: Infrastructure changes (node updates, operator upgrades)
- **Thursday 10pm - 2am ET**: Application changes that require downtime

Outside these windows, changes require additional approval from the SRE Manager.

## Rollback

Every change must have a documented rollback plan. For GitOps-managed changes:

```
# Rollback to previous ArgoCD application version
argocd app rollback <app-name>

# Or revert the Git commit
git revert <commit-hash>
git push
```

For infrastructure changes, the rollback plan should be documented in the Change Request.

## Freeze periods

No production changes during:
- Last 2 weeks of each quarter (code freeze)
- Company-wide events
- Holiday weekends (Thanksgiving, Christmas, New Year)

Exception: P1 incident fixes are always allowed.
