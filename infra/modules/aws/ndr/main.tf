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
    Module    = "ndr"
    ManagedBy = "cortexsim-iac-generator"
  }, var.tags)
}

data "aws_ami" "ubuntu" {
  most_recent = true
  owners      = ["099720109477"]
  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }
  filter {
    name   = "virtualization-type"
    values = ["hvm"]
  }
}

# ----- VPC Flow Logs -----------------------------------------------------

resource "aws_cloudwatch_log_group" "flow" {
  name              = "/cortexsim/${local.name_prefix}/vpc-flow"
  retention_in_days = 7
  tags              = local.common_tags
}

data "aws_iam_policy_document" "flow_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["vpc-flow-logs.amazonaws.com"]
    }
  }
}

data "aws_iam_policy_document" "flow_policy" {
  statement {
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:DescribeLogGroups",
      "logs:DescribeLogStreams",
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role" "flow" {
  name               = "${local.name_prefix}-ndr-flow-role"
  assume_role_policy = data.aws_iam_policy_document.flow_assume.json
  tags               = local.common_tags
}

resource "aws_iam_role_policy" "flow" {
  name   = "${local.name_prefix}-ndr-flow-policy"
  role   = aws_iam_role.flow.id
  policy = data.aws_iam_policy_document.flow_policy.json
}

resource "aws_flow_log" "vpc" {
  iam_role_arn    = aws_iam_role.flow.arn
  log_destination = aws_cloudwatch_log_group.flow.arn
  traffic_type    = "ALL"
  vpc_id          = var.vpc_id
  tags            = merge(local.common_tags, { Name = "${local.name_prefix}-ndr-flow" })

  log_format = "$${version} $${account-id} $${interface-id} $${srcaddr} $${dstaddr} $${srcport} $${dstport} $${protocol} $${packets} $${bytes} $${start} $${end} $${action} $${log-status} $${tcp-flags} $${type} $${pkt-srcaddr} $${pkt-dstaddr}"
}

# ----- Security groups ----------------------------------------------------

# Log collector — accepts HTTP log forwarding from a customer/marketplace NGFW
resource "aws_security_group" "collector" {
  name        = "${local.name_prefix}-ndr-collector-sg"
  description = "CortexSim NDR log collector — HTTP ingest from NGFW"
  vpc_id      = var.vpc_id

  ingress {
    description     = "SSH from jumpbox"
    from_port       = 22
    to_port         = 22
    protocol        = "tcp"
    security_groups = [var.jumpbox_security_group_id]
  }

  ingress {
    description = "HTTP log forwarding from NGFW (port 8080)"
    from_port   = 8080
    to_port     = 8080
    protocol    = "tcp"
    cidr_blocks = [var.dc_ssh_cidr, "10.0.0.0/16"]
  }

  ingress {
    description = "Syslog TCP from NGFW"
    from_port   = 514
    to_port     = 514
    protocol    = "tcp"
    cidr_blocks = [var.dc_ssh_cidr, "10.0.0.0/16"]
  }

  ingress {
    description = "SNMP trap from NGFW"
    from_port   = 162
    to_port     = 162
    protocol    = "udp"
    cidr_blocks = [var.dc_ssh_cidr, "10.0.0.0/16"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.common_tags, { Name = "${local.name_prefix}-ndr-collector-sg" })
}

# Attack endpoint — generates realistic C2/exfil traffic
resource "aws_security_group" "attack_endpoint" {
  name        = "${local.name_prefix}-ndr-attack-sg"
  description = "CortexSim NDR attack endpoint — SSH from jumpbox, outbound any"
  vpc_id      = var.vpc_id

  ingress {
    description     = "SSH from jumpbox"
    from_port       = 22
    to_port         = 22
    protocol        = "tcp"
    security_groups = [var.jumpbox_security_group_id]
  }

  egress {
    description = "Outbound any - attack simulator needs to reach C2 domains"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.common_tags, { Name = "${local.name_prefix}-ndr-attack-sg" })
}

# ----- Log collector EC2 --------------------------------------------------

resource "aws_instance" "collector" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = var.collector_instance_type
  subnet_id              = var.private_subnet_ids[0]
  vpc_security_group_ids = [aws_security_group.collector.id]
  key_name               = var.ssh_key_name

  user_data = <<-EOT
    #!/bin/bash
    set -euo pipefail
    exec > >(tee /var/log/cortexsim-collector-bootstrap.log) 2>&1

    apt-get update -qq
    apt-get install -y python3-pip git build-essential cargo rustc nginx

    # Clone CortexSim source repos (ackbarx = SNMP→HTTP forwarder, mocktaxii = TAXII server)
    git clone --depth=1 https://github.com/gocortexio/ackbarx.git /opt/ackbarx || true
    git clone --depth=1 https://github.com/gocortexio/mocktaxii.git /opt/mocktaxii || true

    # Build ackbarx (best effort)
    if [ -d /opt/ackbarx ]; then
      (cd /opt/ackbarx && cargo build --release 2>/dev/null || true)
    fi

    # A simple nginx-based HTTP log sink on :8080 — NGFWs forward here
    cat > /etc/nginx/sites-available/cortexsim-logsink <<'NGINX'
    server {
      listen 8080 default_server;
      access_log /var/log/nginx/cortexsim-logsink.log;
      location / {
        client_max_body_size 50M;
        proxy_pass http://127.0.0.1:9999;  # local sink
      }
    }
    NGINX
    ln -sf /etc/nginx/sites-available/cortexsim-logsink /etc/nginx/sites-enabled/cortexsim-logsink
    rm -f /etc/nginx/sites-enabled/default
    systemctl reload nginx || true
  EOT

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-ndr-collector"
    Role = "log-collector"
  })
}

# ----- Attack endpoint EC2 (DMZ) ------------------------------------------

resource "aws_instance" "attack_endpoint" {
  ami                         = data.aws_ami.ubuntu.id
  instance_type               = var.attack_endpoint_instance_type
  subnet_id                   = var.public_subnet_ids[0]
  vpc_security_group_ids      = [aws_security_group.attack_endpoint.id]
  key_name                    = var.ssh_key_name
  associate_public_ip_address = true

  user_data = <<-EOT
    #!/bin/bash
    set -euo pipefail
    exec > >(tee /var/log/cortexsim-attack-bootstrap.log) 2>&1

    apt-get update -qq
    apt-get install -y curl git python3-pip netcat-openbsd dnsutils tshark

    # Pull the "testmynids" script set for safe, controlled NIDS triggering
    mkdir -p /opt/cortexsim/attack
    git clone --depth=1 https://github.com/3CORESec/testmynids.org.git /opt/cortexsim/attack/testmynids || true

    # Beaconing helper — loops and makes known-bad looking requests
    cat > /opt/cortexsim/attack/beacon.sh <<'BEACON'
    #!/bin/bash
    # Safe C2 beacon simulator — connects only to documented test domains
    while sleep 120; do
      curl -sSL -m 10 http://testmynids.org/uid/index.html -o /dev/null || true
      # DNS tunneling simulation (short TXT query pattern)
      for i in $(seq 1 5); do
        dig +short TXT "beacon-$(date +%s)-$i.testmynids.org" @1.1.1.1 > /dev/null || true
        sleep 2
      done
    done
    BEACON
    chmod +x /opt/cortexsim/attack/beacon.sh
  EOT

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-ndr-attack-endpoint"
    Role = "attack-endpoint"
  })
}
