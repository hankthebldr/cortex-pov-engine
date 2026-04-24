terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

locals {
  name_prefix = var.project_name
  common_tags = merge({
    Project   = var.project_name
    Module    = "edr"
    ManagedBy = "cortexsim-iac-generator"
  }, var.tags)

  target_amis = [
    # Diverse OS images for realistic EDR testing
    { name = "ubuntu", owner = "099720109477", filter = "ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*" },
    { name = "amzn2", owner = "137112412989", filter = "amzn2-ami-kernel-5.10-hvm-*-x86_64-gp2" },
  ]
}

data "aws_ami" "targets" {
  count       = length(local.target_amis)
  most_recent = true
  owners      = [local.target_amis[count.index].owner]
  filter {
    name   = "name"
    values = [local.target_amis[count.index].filter]
  }
  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

resource "aws_security_group" "target" {
  name        = "${local.name_prefix}-edr-target-sg"
  description = "CortexSim EDR target hosts — SSH from jumpbox only"
  vpc_id      = var.vpc_id

  ingress {
    description     = "SSH from jumpbox"
    from_port       = 22
    to_port         = 22
    protocol        = "tcp"
    security_groups = [var.jumpbox_security_group_id]
  }

  ingress {
    description = "Inter-target (same SG, for lateral movement simulation)"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    self        = true
  }

  egress {
    description = "Outbound any"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.common_tags, { Name = "${local.name_prefix}-edr-target-sg" })
}

resource "aws_instance" "target" {
  count                  = var.target_count
  ami                    = data.aws_ami.targets[count.index % length(local.target_amis)].id
  instance_type          = var.target_size
  subnet_id              = var.private_subnet_ids[count.index % length(var.private_subnet_ids)]
  vpc_security_group_ids = [aws_security_group.target.id]
  key_name               = var.ssh_key_name

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-edr-target-${count.index}"
    OS   = local.target_amis[count.index % length(local.target_amis)].name
  })
}
