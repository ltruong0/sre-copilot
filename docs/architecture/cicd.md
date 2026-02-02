# CI/CD Pipeline

## Overview

We use Tekton Pipelines for CI and ArgoCD for CD (GitOps).

```
Code Push → Tekton Pipeline → Build Image → Push to Quay → ArgoCD Sync → Deploy to Cluster
```

## Tekton Pipelines

Tekton runs in the `ci-pipelines` namespace. Each team has their own PipelineRun resources triggered by webhook events from GitHub Enterprise.

### Standard Pipeline Stages

1. **Clone** - Git clone the repository
2. **Test** - Run unit tests
3. **Build** - Build container image with Buildah
4. **Scan** - Scan image with Trivy for vulnerabilities
5. **Push** - Push image to Quay.io
6. **Update Manifest** - Update the GitOps repo with new image tag

### Triggering a Pipeline

Pipelines are triggered automatically on PR merge to `main` branch.

To trigger manually:

```bash
tkn pipeline start build-and-deploy \
  -p git-url=https://github.acme.com/<team>/<repo> \
  -p git-revision=main \
  -p image-name=quay.io/acme/<app-name> \
  -w name=shared-workspace,claimName=pipeline-pvc \
  -n ci-pipelines
```

Check pipeline status:
```
tkn pipelinerun logs -f -n ci-pipelines
```

## ArgoCD (GitOps)

ArgoCD manages deployments from Git repositories. Each application has an ArgoCD Application resource that points to a Git repo containing Kubernetes manifests or Helm charts.

### Application structure

```
gitops-repo/
├── base/
│   ├── deployment.yaml
│   ├── service.yaml
│   ├── kustomization.yaml
├── overlays/
│   ├── dev/
│   │   └── kustomization.yaml
│   ├── staging/
│   │   └── kustomization.yaml
│   └── prod/
│       └── kustomization.yaml
```

### Promoting between environments

To promote from staging to prod:
1. Update the image tag in `overlays/prod/kustomization.yaml`
2. Create a PR and get approval
3. Merge the PR
4. ArgoCD auto-syncs within 3 minutes

### Manual sync

If auto-sync is disabled or you need to force:

```
argocd app sync <app-name>
```

or from the ArgoCD UI at https://argocd.acme-internal.com

## Image Registry

We use Quay.io (hosted) as our container registry.

- Organization: `acme`
- Robot account for CI: `acme+ci-bot`
- Image naming: `quay.io/acme/<team>-<app>:<git-sha>`

Image retention policy:
- Keep last 10 tags per repository
- Keep all tags less than 30 days old
- Never delete tags matching `v*` (release tags)
