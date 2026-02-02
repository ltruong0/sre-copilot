# Tooling Guide

## Required tools

Install the following on your workstation:

### OpenShift CLI (oc)

Download from the cluster's web console (? icon > Command Line Tools) or:

```bash
# macOS
brew install openshift-cli

# Linux
curl -LO https://mirror.openshift.com/pub/openshift-v4/clients/ocp/stable/openshift-client-linux.tar.gz
tar xvf openshift-client-linux.tar.gz
sudo mv oc /usr/local/bin/
```

Verify: `oc version`

### kubectl

Usually bundled with `oc`, but if you need it separately:

```
brew install kubectl
```

### Helm

```
brew install helm
```

We use Helm 3. Do NOT use Helm 2 (Tiller).

### Tekton CLI (tkn)

```
brew install tektoncd-cli
```

### ArgoCD CLI

```
brew install argocd
```

Login:
```
argocd login argocd.acme-internal.com --sso
```

### Vault CLI

```
brew install vault
```

Configure:
```bash
export VAULT_ADDR=https://vault.acme-internal.com
vault login -method=oidc
```

### jq and yq

Essential for parsing JSON/YAML output:

```
brew install jq yq
```

## Shell setup

Add these to your `~/.bashrc` or `~/.zshrc`:

```bash
# OpenShift aliases
alias k=kubectl
alias kgp='kubectl get pods'
alias kgs='kubectl get svc'
alias kgn='kubectl get nodes'

# Quick namespace switch
alias kns='oc project'

# OpenShift cluster contexts
alias use-dev='oc login https://api.ocp-dev.acme-internal.com:6443'
alias use-staging='oc login https://api.ocp-staging.acme-internal.com:6443'
alias use-prod='oc login https://api.ocp-prod.acme-internal.com:6443'

# Prompt showing current cluster/namespace
export PS1='[\u@\h $(oc project -q 2>/dev/null || echo "no-project")]$ '
```

## IDE setup

We recommend VS Code with these extensions:
- YAML (Red Hat)
- Kubernetes (Microsoft)
- OpenShift Toolkit (Red Hat)
- GitLens

## Git configuration

```bash
git config --global user.name "Your Name"
git config --global user.email "your.name@acme.com"
```

Clone the main repos:
```
git clone https://github.acme.com/platform/gitops-config
git clone https://github.acme.com/platform/sre-docs
git clone https://github.acme.com/platform/ansible-playbooks
```
