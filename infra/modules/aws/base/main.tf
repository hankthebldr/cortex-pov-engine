terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }
}

locals {
  name_prefix = var.project_name
  common_tags = merge({
    Project       = var.project_name
    ManagedBy     = "cortexsim-iac-generator"
  }, var.tags)
}

data "aws_availability_zones" "available" {
  state = "available"
}

# ----- VPC & networking -----------------------------------------------------

resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true
  tags                 = merge(local.common_tags, { Name = "${local.name_prefix}-vpc" })
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = merge(local.common_tags, { Name = "${local.name_prefix}-igw" })
}

resource "aws_subnet" "public" {
  count                   = 2
  vpc_id                  = aws_vpc.main.id
  cidr_block              = cidrsubnet(aws_vpc.main.cidr_block, 8, count.index)
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true
  tags                    = merge(local.common_tags, { Name = "${local.name_prefix}-public-${count.index}" })
}

resource "aws_subnet" "private" {
  count             = 2
  vpc_id            = aws_vpc.main.id
  cidr_block        = cidrsubnet(aws_vpc.main.cidr_block, 8, count.index + 10)
  availability_zone = data.aws_availability_zones.available.names[count.index]
  tags              = merge(local.common_tags, { Name = "${local.name_prefix}-private-${count.index}" })
}

resource "aws_eip" "nat" {
  domain = "vpc"
  tags   = merge(local.common_tags, { Name = "${local.name_prefix}-nat-eip" })
}

resource "aws_nat_gateway" "main" {
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public[0].id
  tags          = merge(local.common_tags, { Name = "${local.name_prefix}-nat" })
  depends_on    = [aws_internet_gateway.main]
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
  tags = merge(local.common_tags, { Name = "${local.name_prefix}-public-rt" })
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main.id
  }
  tags = merge(local.common_tags, { Name = "${local.name_prefix}-private-rt" })
}

resource "aws_route_table_association" "public" {
  count          = length(aws_subnet.public)
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

resource "aws_route_table_association" "private" {
  count          = length(aws_subnet.private)
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

# ----- Security group for jumpbox -----------------------------------------

resource "aws_security_group" "jumpbox" {
  name        = "${local.name_prefix}-jumpbox-sg"
  description = "CortexSim jumpbox: SSH from DC, SimCore UI from DC"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "SSH from DC"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.dc_ssh_cidr]
  }

  ingress {
    description = "SimCore UI from DC"
    from_port   = 8888
    to_port     = 8888
    protocol    = "tcp"
    cidr_blocks = [var.dc_ssh_cidr]
  }

  egress {
    description = "Outbound any"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.common_tags, { Name = "${local.name_prefix}-jumpbox-sg" })
}

# ----- SSH keypair (generated, stored in SSM) -----------------------------

resource "tls_private_key" "jumpbox" {
  algorithm = "ED25519"
}

resource "aws_key_pair" "jumpbox" {
  key_name   = "${local.name_prefix}-jumpbox"
  public_key = tls_private_key.jumpbox.public_key_openssh
  tags       = local.common_tags
}

resource "aws_ssm_parameter" "jumpbox_private_key" {
  name        = "/cortexsim/${local.name_prefix}/jumpbox-ssh-key"
  description = "Private SSH key for the CortexSim jumpbox"
  type        = "SecureString"
  value       = tls_private_key.jumpbox.private_key_openssh
  tags        = local.common_tags
}

# ----- Jumpbox EC2 --------------------------------------------------------

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }
  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

resource "aws_instance" "jumpbox" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.jumpbox_size
  subnet_id              = aws_subnet.public[0].id
  vpc_security_group_ids = [aws_security_group.jumpbox.id]
  key_name               = aws_key_pair.jumpbox.key_name

  user_data = templatefile("${path.module}/userdata.sh.tftpl", {
    content_modules = join(",", var.content_modules)
    project_name    = var.project_name
  })

  root_block_device {
    volume_size = 30
    volume_type = "gp3"
    encrypted   = true
  }

  tags = merge(local.common_tags, { Name = "${local.name_prefix}-jumpbox" })
}
