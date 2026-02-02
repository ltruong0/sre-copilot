---
title: networking architecture
---

# Networking

## Overview

Our networking stack is built on OVN-Kubernetes (the default for OpenShift 4.x). This doc covers the key networking concepts and how traffic flows through our platform.

## Network Topology

```
Internet → IBM Cloud Load Balancer → OpenShift Router (HAProxy) → Service → Pods
```

### External Access

External traffic enters through IBM Cloud Load Balancers which forward to the OpenShift router pods running in the `openshift-ingress` namespace. The router uses SNI to route to the correct backend based on the hostname.

DNS is managed in IBM Cloud Internet Services (CIS). We use a wildcard DNS entry `*.apps.acme-internal.com` pointing to the load balancer VIP.

### Internal networking

Pod-to-pod communication goes through OVN-Kubernetes overlay network. Each pod gets an IP from the cluster network CIDR.

Key CIDRs:

    Cluster Network: 10.128.0.0/14
    Service Network: 172.30.0.0/16  
    Machine Network: 10.0.0.0/16

### Network Policies

We enforce namespace isolation using NetworkPolicy objects. Default deny-all is applied to every namespace:

```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: deny-all
spec:
  podSelector: {}
  policyTypes:
  - Ingress
  - Egress
```

Teams must explicitly allow traffic by creating NetworkPolicy resources. Common patterns:

1. Allow from same namespace:
```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-same-namespace
spec:
  podSelector: {}
  ingress:
  - from:
    - podSelector: {}
```

2. Allow from ingress controller:
```yaml
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata:
  name: allow-from-router
spec:
  podSelector: {}
  ingress:
  - from:
    - namespaceSelector:
        matchLabels:
          network.openshift.io/policy-group: ingress
```

## Service Mesh

We run OpenShift Service Mesh (based on Istio) for teams that need advanced traffic management.

Service mesh features in use:
- mTLS between services
- Traffic splitting for canary deployments
- Circuit breaking
- Distributed tracing via Jaeger

To onboard to the mesh, teams add their namespace to the `ServiceMeshMemberRoll` in the `istio-system` namespace. See the onboarding guide for details.

## DNS Resolution

Internal DNS is handled by CoreDNS pods in the `openshift-dns` namespace.

Service discovery follows the standard Kubernetes pattern:

```
<service-name>.<namespace>.svc.cluster.local
```

For external DNS resolution we use IBM Cloud DNS servers with forwarders for internal domains.

Troubleshooting DNS issues: see [DNS Resolution guide](../troubleshooting/dns-resolution.md)

## Egress

By default pods can reach the internet through NAT. For security-sensitive namespaces we use EgressNetworkPolicy to restrict outbound traffic:

```
apiVersion: network.openshift.io/v1
kind: EgressNetworkPolicy
metadata:
  name: default-egress
  namespace: payments-prod
spec:
  egress:
  - type: Allow
    to:
      cidrSelector: 10.0.0.0/8
  - type: Allow
    to:
      dnsName: api.stripe.com
  - type: Deny
    to:
      cidrSelector: 0.0.0.0/0
```
