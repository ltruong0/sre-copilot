# On-Call Rotation

## Schedule

We maintain a 24/7 on-call rotation for the SRE team.

- Primary on-call: 1 week rotation (Monday 9am to Monday 9am ET)
- Secondary on-call: backup for the primary, same rotation offset by 1 week
- Rotation managed in PagerDuty schedule: "SRE Platform Primary"

## Responsibilities

The on-call engineer is responsible for:

1. **Responding to alerts** within the SLA defined in the [Incident Response Policy](incident-response.md)
2. **Triaging issues** and determining severity
3. **Resolving or escalating** based on the runbooks
4. **Updating the team** on any incidents during handoff
5. **Handing off cleanly** to the next on-call at rotation time

## What to monitor

During your on-call shift, keep an eye on:

- PagerDuty app on your phone (ensure notifications are on)
- #sre-alerts Slack channel
- Grafana dashboards:
  - Cluster Health Overview
  - Node Status
  - Pod Health
  - Certificate Expiry

## On-call Handoff

At the start of your rotation:
1. Check the on-call handoff doc (Confluence: SRE > On-Call Handoff)
2. Read notes from the previous on-call
3. Check for any ongoing incidents or known issues
4. Verify your PagerDuty app is working (send a test notification)

At the end of your rotation:
1. Update the handoff doc with:
   - Any incidents that occurred
   - Ongoing issues or things to watch
   - Any changes to the environment
2. Brief the next on-call if there are active issues

## Compensation

- On-call engineers receive a stipend of $X per week of on-call duty
- If paged outside business hours and it takes >30 minutes, log comp time
- Details in the HR policy document

## Burnout prevention

- No back-to-back on-call weeks
- If your on-call shift was particularly heavy (5+ pages overnight), talk to your manager about time off
- We aim for a minimum of 4 weeks between on-call rotations per person
- If the rotation is too frequent, we need to hire — flag this to the SRE Manager

## Useful commands during on-call

Quick cluster health check:

```bash
# Check all cluster operators
oc get co

# Check node status  
oc get nodes

# Check pods not in Running state
oc get pods --all-namespaces --field-selector status.phase!=Running,status.phase!=Succeeded

# Check recent events
oc get events --all-namespaces --sort-by=.lastTimestamp | tail -20

# Check certificate expiry
oc get certificates --all-namespaces | grep -v True
```
