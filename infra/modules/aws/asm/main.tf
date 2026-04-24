terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

locals {
  name_prefix = var.project_name
  common_tags = merge({
    Project   = var.project_name
    Module    = "asm"
    ManagedBy = "cortexsim-iac-generator"
    Purpose   = "exposed-surface-for-asm-discovery"
  }, var.tags)
}

resource "random_id" "bucket_suffix" {
  byte_length = 4
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
# EXPOSED EC2 - Multiple services on non-standard ports
# =========================================================================

resource "aws_security_group" "exposed" {
  name        = "${local.name_prefix}-asm-exposed-sg"
  description = "ASM module - intentionally exposed services for surface discovery"
  vpc_id      = var.vpc_id

  ingress {
    description = "SSH on non-standard 2222"
    from_port   = 2222
    to_port     = 2222
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTP (nginx directory listing)"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "HTTPS (self-signed / weak TLS cipher config)"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "gocortexbrokenbank vulnerable app"
    from_port   = 9001
    to_port     = 9001
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "Elasticsearch-style default port"
    from_port   = 9200
    to_port     = 9200
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "Redis default port"
    from_port   = 6379
    to_port     = 6379
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.common_tags, {
    Name                = "${local.name_prefix}-asm-exposed-sg"
    CortexSimASMFinding = "multiple-exposed-ports"
  })
}

resource "aws_instance" "exposed" {
  ami                         = data.aws_ami.ubuntu.id
  instance_type               = var.exposed_instance_type
  subnet_id                   = var.public_subnet_ids[0]
  vpc_security_group_ids      = [aws_security_group.exposed.id]
  associate_public_ip_address = true

  user_data = <<-EOT
    #!/bin/bash
    set -euo pipefail
    exec > >(tee /var/log/cortexsim-asm-bootstrap.log) 2>&1

    apt-get update -qq
    apt-get install -y nginx python3 python3-pip openssl git curl jq net-tools redis-server

    # ----- SSH on 2222 (non-standard port finding) -----
    cp /etc/ssh/sshd_config /etc/ssh/sshd_config.bak
    sed -i 's/#Port 22/Port 2222/' /etc/ssh/sshd_config
    # Allow password auth to trip "weak authentication" findings
    sed -i 's/#PasswordAuthentication yes/PasswordAuthentication yes/' /etc/ssh/sshd_config
    systemctl restart sshd || systemctl restart ssh || true

    # ----- Nginx with directory listing enabled + default server -----
    mkdir -p /var/www/cortexsim-asm
    cat > /var/www/cortexsim-asm/index.html <<'HTML'
    <html><body><h1>CortexSim ASM Target</h1>
    <p>Intentional exposed surface for Cortex ASM validation. Not real data.</p>
    <ul><li><a href="/admin/">/admin/</a></li><li><a href="/config/">/config/</a></li><li><a href="/backup/">/backup/</a></li></ul>
    </body></html>
    HTML
    mkdir -p /var/www/cortexsim-asm/admin /var/www/cortexsim-asm/config /var/www/cortexsim-asm/backup
    echo "admin:admin" > /var/www/cortexsim-asm/admin/credentials.txt.bak
    echo "database.url=jdbc:postgresql://db/prod" > /var/www/cortexsim-asm/config/app.properties
    echo "dump_data_2024_placeholder" > /var/www/cortexsim-asm/backup/dump.sql

    cat > /etc/nginx/sites-available/default <<'NGINX'
    server {
      listen 80 default_server;
      root /var/www/cortexsim-asm;
      autoindex on;
      autoindex_exact_size off;
      server_tokens on;
      location / { try_files $uri $uri/ =404; }
    }
    NGINX
    systemctl reload nginx

    # ----- Self-signed TLS with weak cipher exposure -----
    openssl req -x509 -newkey rsa:1024 -keyout /etc/ssl/private/cortexsim-weak.key \
      -out /etc/ssl/certs/cortexsim-weak.crt -days 30 -nodes -subj "/CN=cortexsim-asm-test"
    cat > /etc/nginx/sites-available/weak-tls <<'NGINX'
    server {
      listen 443 ssl default_server;
      ssl_certificate /etc/ssl/certs/cortexsim-weak.crt;
      ssl_certificate_key /etc/ssl/private/cortexsim-weak.key;
      ssl_protocols TLSv1 TLSv1.1 TLSv1.2;
      ssl_ciphers HIGH:!aNULL:!MD5;
      root /var/www/cortexsim-asm;
    }
    NGINX
    ln -sf /etc/nginx/sites-available/weak-tls /etc/nginx/sites-enabled/weak-tls
    systemctl reload nginx || true

    # ----- gocortexbrokenbank on :9001 -----
    git clone --depth=1 https://github.com/gocortexio/gocortexbrokenbank.git /opt/brokenbank || true
    if [ -d /opt/brokenbank ]; then
      cd /opt/brokenbank
      pip3 install -r requirements.txt 2>/dev/null || true
      # Run as service
      cat > /etc/systemd/system/brokenbank.service <<'UNIT'
    [Unit]
    Description=gocortexbrokenbank vulnerable app
    After=network.target
    [Service]
    ExecStart=/usr/bin/python3 /opt/brokenbank/app.py --port 9001
    WorkingDirectory=/opt/brokenbank
    Restart=always
    [Install]
    WantedBy=multi-user.target
    UNIT
      systemctl daemon-reload
      systemctl enable --now brokenbank 2>/dev/null || true
    fi

    # ----- Redis bound to 0.0.0.0 with no auth (huge ASM finding) -----
    sed -i 's/bind 127.0.0.1.*/bind 0.0.0.0/' /etc/redis/redis.conf
    sed -i 's/^protected-mode yes/protected-mode no/' /etc/redis/redis.conf
    systemctl restart redis-server

    # ----- Fake Elasticsearch response on :9200 -----
    cat > /opt/fake-es.py <<'PY'
    from http.server import HTTPServer, BaseHTTPRequestHandler
    import json
    class H(BaseHTTPRequestHandler):
      def do_GET(self):
        self.send_response(200)
        self.send_header('Content-Type','application/json')
        self.end_headers()
        self.wfile.write(json.dumps({"name":"cortexsim-asm","cluster_name":"cortexsim","version":{"number":"1.7.0"}}).encode())
      def log_message(self, *a, **k): pass
    HTTPServer(('0.0.0.0',9200), H).serve_forever()
    PY
    nohup python3 /opt/fake-es.py > /var/log/fake-es.log 2>&1 &

    echo "[cortexsim-asm] bootstrap complete"
  EOT

  root_block_device {
    volume_size = 20
    volume_type = "gp3"
    encrypted   = true
  }

  tags = merge(local.common_tags, {
    Name                = "${local.name_prefix}-asm-exposed"
    Role                = "exposed-attack-surface"
    CortexSimASMFinding = "multi-service-exposed-host"
  })
}

# =========================================================================
# PUBLIC S3 WEBSITE BUCKET (static website hosting enabled + public)
# =========================================================================

resource "aws_s3_bucket" "website" {
  bucket        = "${local.name_prefix}-asm-website-${random_id.bucket_suffix.hex}"
  force_destroy = true
  tags = merge(local.common_tags, {
    Name                = "${local.name_prefix}-asm-website"
    CortexSimASMFinding = "public-s3-website"
  })
}

resource "aws_s3_bucket_public_access_block" "website" {
  bucket                  = aws_s3_bucket.website.id
  block_public_acls       = false
  block_public_policy     = false
  ignore_public_acls      = false
  restrict_public_buckets = false
}

resource "aws_s3_bucket_website_configuration" "website" {
  bucket = aws_s3_bucket.website.id
  index_document { suffix = "index.html" }
}

resource "aws_s3_bucket_policy" "website" {
  bucket     = aws_s3_bucket.website.id
  depends_on = [aws_s3_bucket_public_access_block.website]
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "PublicReadGetObject"
      Effect    = "Allow"
      Principal = "*"
      Action    = "s3:GetObject"
      Resource  = "${aws_s3_bucket.website.arn}/*"
    }]
  })
}

resource "aws_s3_object" "index" {
  bucket       = aws_s3_bucket.website.id
  key          = "index.html"
  content      = "<html><body><h1>CortexSim ASM - Public S3 Website</h1><p>Test surface for Cortex ASM discovery.</p></body></html>"
  content_type = "text/html"
  tags         = local.common_tags
}
