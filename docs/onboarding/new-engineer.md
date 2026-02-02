# New Engineer Onboarding

Welcome to the Acme Corp SRE team! This guide will help you get set up.

## Day 1 Checklist

- [ ] Get your laptop set up (see IT onboarding email)
- [ ] Join Slack channels: #sre-platform, #sre-alerts, #incidents, #general
- [ ] Request access to OpenShift clusters (see [Access Requests](access-requests.md))
- [ ] Set up your development environment (see [Tooling Guide](tooling.md))
- [ ] Read the [Platform Overview](../architecture/platform-overview.md)
- [ ] Complete PagerDuty onboarding (your manager will add you)
- [ ] Review the [Incident Response Policy](../policies/incident-response.md)

## Week 1 Goals

1. Shadow an on-call engineer for at least 2 days
2. Complete all access requests and verify you can log into each cluster
3. Run through the "hello world" deployment exercise (below)
4. Read through all runbooks in the Runbooks section
5. Attend the weekly SRE standup (Tuesdays 10am ET)

## Hello World Deployment Exercise

Deploy a test application to the dev cluster to verify your access and tooling:

```bash
# Log in to the dev cluster
oc login https://api.ocp-dev.acme-internal.com:6443

# Create a test project
oc new-project <your-name>-sandbox

# Deploy a test app
oc new-app --name=hello httpd:2.4-el9

# Expose the service
oc expose svc/hello

# Get the route URL
oc get route hello -o jsonpath='{.spec.host}'
```

Visit the URL in your browser — you should see the Apache test page.

Clean up when done:

```
oc delete project <your-name>-sandbox
```

## Key contacts

Your buddy during onboarding: assigned by your manager
Team lead: check your team's Confluence page  
SRE manager: see org chart in Workday

## Learning Resources

- OpenShift documentation: https://docs.openshift.com
- Kubernetes the Hard Way: https://github.com/kelseyhightower/kubernetes-the-hard-way
- SRE Book (Google): https://sre.google/sre-book/table-of-contents/
- Internal training videos: SharePoint > SRE Team > Training
