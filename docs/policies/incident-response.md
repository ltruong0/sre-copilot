# Incident Response Policy

## Severity Levels

| Severity | Description | Response Time | Resolution Target |
|----------|------------|---------------|-------------------|
| P1 - Critical | Customer-facing service down, data loss risk | 5 minutes | 1 hour |
| P2 - High | Degraded service, partial outage | 15 minutes | 4 hours |
| P3 - Medium | Non-critical service affected, workaround exists | 1 hour | 24 hours |
| P4 - Low | Minor issue, no user impact | 4 hours | 1 week |

## Incident Lifecycle

### 1. Detection

Incidents are detected via:
- Automated alerts (PagerDuty, Prometheus AlertManager)
- Customer reports (#support channel)
- Internal observation

### 2. Triage

The on-call engineer must:
1. Acknowledge the PagerDuty alert within the response time SLA
2. Create an incident channel: `#inc-YYYYMMDD-short-description`
3. Post initial assessment in the channel
4. Assign severity level
5. Begin investigation

### 3. Communication

**For P1/P2 incidents:**
- Post status updates every 15 minutes in the incident channel
- Notify stakeholders via the #incidents channel
- Update the status page if customer-facing

**For P3/P4:**
- Post updates as available
- No status page update needed

### 4. Resolution

Once the immediate issue is resolved:
1. Verify the fix is stable for at least 15 minutes
2. Post all-clear in the incident channel
3. Update PagerDuty incident to resolved
4. Update status page if applicable

### 5. Post-Incident

Within 48 hours of resolution:
1. Schedule a blameless post-mortem meeting
2. Write a post-mortem document using the template (Confluence > SRE > Post-Mortems)
3. Identify action items and assign owners
4. Track action items in Jira project SRE-POSTMORTEM

## Escalation Path

```
On-call Engineer
  ↓ (15 min no progress on P1)
Team Lead
  ↓ (30 min no progress on P1)
SRE Manager
  ↓ (1 hour no progress on P1)
VP of Engineering
```

## War Room Rules

For P1 incidents, a war room may be convened:
- Only essential personnel in the call
- One incident commander (IC) leads
- IC delegates investigation tasks
- One person handles communication (status updates)
- Keep the line clear — use Slack for side conversations
- Document all actions taken with timestamps

## Tools

| Tool | Purpose | URL |
|------|---------|-----|
| PagerDuty | Alerting and on-call | https://acme.pagerduty.com |
| Grafana | Metrics dashboards | https://grafana.acme-internal.com |
| Loki | Log queries | via Grafana |
| Jaeger | Distributed tracing | https://jaeger.acme-internal.com |
| Status Page | External communication | https://status.acme.com |
