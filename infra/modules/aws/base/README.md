---
name: base
description: VPC, jumpbox with SimCore, security groups, NAT, SSH keypair. Always deployed.
providers: [aws]
required_params: [project_name, dc_ssh_cidr]
optional_params: [jumpbox_size, tags]
dependencies: []
---

# base (AWS)

Provisions the foundational CortexSim environment on AWS:

- VPC with 2 public + 2 private subnets across 2 AZs
- Internet gateway, NAT gateway
- Security group allowing SSH (22) and SimCore UI (8888) from the DC's CIDR
- Jumpbox EC2 (Ubuntu 22.04) with SimCore + content installer running at boot
- SSH keypair (generated in-place; private key stored in AWS SSM Parameter Store)

## Outputs consumed by other modules

- `vpc_id`, `public_subnet_ids`, `private_subnet_ids`
- `jumpbox_security_group_id`
- `ssh_key_name`

## Accessing the jumpbox

After `terraform apply`, retrieve the SSH private key:

```bash
aws ssm get-parameter --name /cortexsim/<project_name>/jumpbox-ssh-key \
  --with-decryption --query Parameter.Value --output text > jumpbox.pem
chmod 600 jumpbox.pem
ssh -i jumpbox.pem ubuntu@$(terraform output -raw jumpbox_public_ip)
```
