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
    Module    = "tim"
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

# =========================================================================
# TIM host - mocktaxii (TAXII 2.1 server) + fake C2 HTTP endpoint
# =========================================================================

resource "aws_security_group" "tim" {
  name        = "${local.name_prefix}-tim-sg"
  description = "CortexSim TIM host - TAXII server + fake C2 endpoint"
  vpc_id      = var.vpc_id

  ingress {
    description     = "SSH from jumpbox"
    from_port       = 22
    to_port         = 22
    protocol        = "tcp"
    security_groups = [var.jumpbox_security_group_id]
  }

  ingress {
    description = "TAXII 2.1 server (mocktaxii default port 9000)"
    from_port   = 9000
    to_port     = 9000
    protocol    = "tcp"
    cidr_blocks = [var.dc_ssh_cidr]
  }

  ingress {
    description = "Fake C2 HTTP endpoint for IOC callback testing"
    from_port   = 8000
    to_port     = 8000
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.common_tags, { Name = "${local.name_prefix}-tim-sg" })
}

resource "aws_instance" "tim" {
  ami                         = data.aws_ami.ubuntu.id
  instance_type               = var.tim_instance_type
  subnet_id                   = var.public_subnet_ids[0]
  vpc_security_group_ids      = [aws_security_group.tim.id]
  key_name                    = var.ssh_key_name
  associate_public_ip_address = true

  user_data = <<-EOT
    #!/bin/bash
    set -euo pipefail
    exec > >(tee /var/log/cortexsim-tim-bootstrap.log) 2>&1

    apt-get update -qq
    apt-get install -y python3 python3-pip git curl

    # ----- mocktaxii (TAXII 2.1 server) -----
    git clone --depth=1 https://github.com/gocortexio/mocktaxii.git /opt/mocktaxii || true
    if [ -d /opt/mocktaxii ]; then
      cd /opt/mocktaxii
      pip3 install -r requirements.txt 2>/dev/null || true

      cat > /etc/systemd/system/mocktaxii.service <<'UNIT'
    [Unit]
    Description=mocktaxii TAXII 2.1 server
    After=network.target
    [Service]
    ExecStart=/usr/bin/python3 /opt/mocktaxii/main.py --port 9000
    WorkingDirectory=/opt/mocktaxii
    Restart=always
    [Install]
    WantedBy=multi-user.target
    UNIT
      systemctl daemon-reload
      systemctl enable --now mocktaxii 2>/dev/null || true
    fi

    # ----- Fake C2 endpoint for IOC callback testing -----
    # This is the target that XSIAM's TIM IOC feed will flag when endpoints reach it.
    cat > /opt/fake-c2.py <<'PY'
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import json, datetime
    class H(BaseHTTPRequestHandler):
      def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type','application/json')
        self.send_header('Server','nginx/1.18.0')
        self.end_headers()
        resp = {
          "cortexsim_tim_test": True,
          "timestamp": datetime.datetime.utcnow().isoformat(),
          "note": "Known-bad test endpoint for Cortex TIM validation. Not real C2."
        }
        self.wfile.write(json.dumps(resp).encode())
      def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        _ = self.rfile.read(length)
        self.send_response(200)
        self.send_header('Content-Type','text/plain')
        self.end_headers()
        self.wfile.write(b'OK')
      def log_message(self, *a, **k):
        print(f"[{datetime.datetime.utcnow().isoformat()}] {self.client_address[0]} {self.command} {self.path}")
    HTTPServer(('0.0.0.0', 8000), H).serve_forever()
    PY

    cat > /etc/systemd/system/fake-c2.service <<'UNIT'
    [Unit]
    Description=CortexSim fake C2 endpoint for TIM validation
    After=network.target
    [Service]
    ExecStart=/usr/bin/python3 /opt/fake-c2.py
    StandardOutput=append:/var/log/fake-c2.log
    StandardError=append:/var/log/fake-c2.log
    Restart=always
    [Install]
    WantedBy=multi-user.target
    UNIT
    systemctl daemon-reload
    systemctl enable --now fake-c2

    echo "[cortexsim-tim] bootstrap complete"
  EOT

  root_block_device {
    volume_size = 20
    volume_type = "gp3"
    encrypted   = true
  }

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-tim"
    Role = "tim-server"
  })
}

# =========================================================================
# Route 53 private zone with planted known-bad-looking records
# =========================================================================

resource "aws_route53_zone" "tim_private" {
  name = "${local.name_prefix}-tim.internal"
  vpc {
    vpc_id = var.vpc_id
  }
  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-tim-zone"
  })
}

# Planted IOC-style subdomains — each resolves to the fake C2 endpoint
# so endpoint beacons to these names will both match TIM IOC feeds AND
# actually produce outbound network traffic for NDR stitching
resource "aws_route53_record" "ioc_records" {
  for_each = toset([
    "c2-beacon",
    "exfil-drop",
    "payload-delivery",
    "dga-1a2b3c",
    "cryptominer-pool",
  ])
  zone_id = aws_route53_zone.tim_private.id
  name    = "${each.key}.${aws_route53_zone.tim_private.name}"
  type    = "A"
  ttl     = 60
  records = [aws_instance.tim.public_ip]
}
