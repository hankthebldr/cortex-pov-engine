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
    Module    = "cspm"
    ManagedBy = "cortexsim-iac-generator"
    Purpose   = "intentional-misconfig-for-cspm-validation"
  }, var.tags)
}

data "aws_caller_identity" "current" {}

resource "random_id" "bucket_suffix" {
  byte_length = 4
}

# =========================================================================
# S3 MISCONFIGURATIONS
# =========================================================================

# Finding: Public S3 bucket (most common CSPM finding in the wild)
resource "aws_s3_bucket" "public" {
  bucket        = "${local.name_prefix}-cspm-public-${random_id.bucket_suffix.hex}"
  force_destroy = true
  tags = merge(local.common_tags, {
    Name                = "${local.name_prefix}-cspm-public"
    CortexSimCSPMFinding = "public-read-bucket"
  })
}

resource "aws_s3_bucket_public_access_block" "public" {
  bucket                  = aws_s3_bucket.public.id
  block_public_acls       = false
  block_public_policy     = false
  ignore_public_acls      = false
  restrict_public_buckets = false
}

resource "aws_s3_bucket_ownership_controls" "public" {
  bucket = aws_s3_bucket.public.id
  rule { object_ownership = "BucketOwnerPreferred" }
}

resource "aws_s3_bucket_acl" "public" {
  depends_on = [
    aws_s3_bucket_ownership_controls.public,
    aws_s3_bucket_public_access_block.public,
  ]
  bucket = aws_s3_bucket.public.id
  acl    = "public-read"
}

# Add a dummy object so the bucket isn't empty (realistic finding)
resource "aws_s3_object" "dummy" {
  bucket  = aws_s3_bucket.public.id
  key     = "README.txt"
  content = "CortexSim test data - intentionally public to trigger CSPM detection. Not real customer data."
  tags    = local.common_tags
}

# Finding: Bucket with versioning disabled (compliance finding)
resource "aws_s3_bucket" "unversioned" {
  bucket        = "${local.name_prefix}-cspm-unversioned-${random_id.bucket_suffix.hex}"
  force_destroy = true
  tags = merge(local.common_tags, {
    Name                = "${local.name_prefix}-cspm-unversioned"
    CortexSimCSPMFinding = "versioning-disabled"
  })
}

resource "aws_s3_bucket_versioning" "unversioned" {
  bucket = aws_s3_bucket.unversioned.id
  versioning_configuration {
    status = "Disabled"
  }
}

# Finding: Bucket with no server-side encryption configured
# (AWS defaults to SSE-S3 in 2023+, but absence of explicit KMS config
#  is still a common CSPM finding)
resource "aws_s3_bucket" "no_kms" {
  bucket        = "${local.name_prefix}-cspm-no-kms-${random_id.bucket_suffix.hex}"
  force_destroy = true
  tags = merge(local.common_tags, {
    Name                = "${local.name_prefix}-cspm-no-kms"
    CortexSimCSPMFinding = "no-customer-managed-kms"
  })
}

# =========================================================================
# SECURITY GROUP MISCONFIGURATIONS
# =========================================================================

# Finding: SG allowing SSH from 0.0.0.0/0
resource "aws_security_group" "ssh_open_world" {
  name        = "${local.name_prefix}-cspm-ssh-open"
  description = "Intentionally misconfigured - SSH open to world for CSPM validation"
  vpc_id      = var.vpc_id

  ingress {
    description = "SSH from ANYWHERE - intentional CSPM finding"
    from_port   = 22
    to_port     = 22
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
    Name                = "${local.name_prefix}-cspm-ssh-open"
    CortexSimCSPMFinding = "ssh-open-to-world"
  })
}

# Finding: SG allowing a database port from 0.0.0.0/0
resource "aws_security_group" "db_open_world" {
  name        = "${local.name_prefix}-cspm-db-open"
  description = "Intentionally misconfigured - database ports open for CSPM validation"
  vpc_id      = var.vpc_id

  ingress {
    description = "MySQL from ANYWHERE - intentional CSPM finding"
    from_port   = 3306
    to_port     = 3306
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "Postgres from ANYWHERE - intentional CSPM finding"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  ingress {
    description = "Redis from ANYWHERE - intentional CSPM finding"
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
    Name                = "${local.name_prefix}-cspm-db-open"
    CortexSimCSPMFinding = "database-port-open-to-world"
  })
}

# =========================================================================
# IAM MISCONFIGURATIONS
# =========================================================================

# Finding: IAM role with AdministratorAccess managed policy (overly permissive)
resource "aws_iam_role" "admin_role" {
  name = "${local.name_prefix}-cspm-admin-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "ec2.amazonaws.com"
      }
    }]
  })
  tags = merge(local.common_tags, {
    CortexSimCSPMFinding = "role-with-admin-access"
  })
}

resource "aws_iam_role_policy_attachment" "admin_role" {
  role       = aws_iam_role.admin_role.name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}

# Finding: IAM user with inline wildcard policy (iam:* is a top CSPM finding)
resource "aws_iam_user" "overprivileged" {
  name          = "${local.name_prefix}-cspm-overprivileged-user"
  force_destroy = true
  tags = merge(local.common_tags, {
    CortexSimCSPMFinding = "user-with-wildcard-iam"
  })
}

resource "aws_iam_user_policy" "overprivileged" {
  name = "wildcard-iam-policy"
  user = aws_iam_user.overprivileged.name
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "iam:*"
      Resource = "*"
    }]
  })
}

# =========================================================================
# EBS / EC2 MISCONFIGURATIONS
# =========================================================================

data "aws_availability_zones" "available" {
  state = "available"
}

# Finding: Unencrypted EBS volume attached to nothing (orphaned + unencrypted)
resource "aws_ebs_volume" "unencrypted" {
  availability_zone = data.aws_availability_zones.available.names[0]
  size              = 1
  encrypted         = false
  tags = merge(local.common_tags, {
    Name                = "${local.name_prefix}-cspm-unencrypted-vol"
    CortexSimCSPMFinding = "ebs-unencrypted"
  })
}

# =========================================================================
# CLOUDTRAIL / LOGGING GAPS
# =========================================================================

# Finding: CloudTrail with log file validation disabled
resource "aws_s3_bucket" "cloudtrail" {
  bucket        = "${local.name_prefix}-cspm-cloudtrail-${random_id.bucket_suffix.hex}"
  force_destroy = true
  tags          = local.common_tags
}

resource "aws_s3_bucket_policy" "cloudtrail" {
  bucket = aws_s3_bucket.cloudtrail.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AWSCloudTrailAclCheck"
        Effect    = "Allow"
        Principal = { Service = "cloudtrail.amazonaws.com" }
        Action    = "s3:GetBucketAcl"
        Resource  = aws_s3_bucket.cloudtrail.arn
      },
      {
        Sid       = "AWSCloudTrailWrite"
        Effect    = "Allow"
        Principal = { Service = "cloudtrail.amazonaws.com" }
        Action    = "s3:PutObject"
        Resource  = "${aws_s3_bucket.cloudtrail.arn}/AWSLogs/${data.aws_caller_identity.current.account_id}/*"
        Condition = {
          StringEquals = {
            "s3:x-amz-acl" = "bucket-owner-full-control"
          }
        }
      },
    ]
  })
}

resource "aws_cloudtrail" "weak" {
  name                          = "${local.name_prefix}-cspm-trail"
  s3_bucket_name                = aws_s3_bucket.cloudtrail.id
  enable_log_file_validation    = false # intentional finding
  include_global_service_events = false # intentional finding
  is_multi_region_trail         = false # intentional finding
  depends_on                    = [aws_s3_bucket_policy.cloudtrail]
  tags = merge(local.common_tags, {
    CortexSimCSPMFinding = "cloudtrail-weak-config"
  })
}
