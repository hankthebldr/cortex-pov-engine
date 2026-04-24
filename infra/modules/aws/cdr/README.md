---
name: cdr
description: EKS cluster with worker nodes for container/Kubernetes detection scenarios
providers: [aws]
required_params: [project_name]
optional_params: [node_count, node_size, k8s_version]
dependencies: [base]
---

# cdr (AWS)

Provisions an EKS cluster with a managed node group inside the base VPC's private subnets.

## Content installed

Attack: deepce, botb, kube-hunter, light-k8s-attack-simulations, KubeHound.
Defense: falco + falco-rules, tetragon, tracee, trivy, grype.

## Connecting kubectl

```bash
aws eks update-kubeconfig --region <region> --name <project_name>-cdr
kubectl get nodes
```
