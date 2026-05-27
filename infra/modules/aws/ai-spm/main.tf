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
    Module    = "ai-spm"
    ManagedBy = "cortexsim-iac-generator"
    Purpose   = "intentional-ai-misconfig-for-aispm-validation"
  }, var.tags)
}

data "aws_caller_identity" "current" {}

resource "random_id" "suffix" {
  byte_length = 4
}

# =========================================================================
# TRAINING DATA — S3 bucket with PII / PHI / PCI canary fixtures
# Covers TC-AISP-05 (Sensitive Data Classification).
# =========================================================================

resource "aws_s3_bucket" "training_data" {
  bucket        = "${local.name_prefix}-aispm-training-data-${random_id.suffix.hex}"
  force_destroy = true
  tags = merge(local.common_tags, {
    Name                  = "${local.name_prefix}-aispm-training-data"
    CortexSimAISPMFinding = "training-data-with-regulated-pii"
    AIWorkload            = "true"
  })
}

resource "aws_s3_object" "training_pii_fixture" {
  bucket  = aws_s3_bucket.training_data.id
  key     = "training/customer_profiles.csv"
  content = <<-EOT
    user_id,full_name,ssn,credit_card,health_record_id,medical_dx
    1001,Jane Canary,000-00-0001,4111-1111-1111-1111,MRN-CORTEXSIM-001,DIAGNOSIS-CANARY-PCI
    1002,Bob Test,000-00-0002,4111-1111-1111-1112,MRN-CORTEXSIM-002,DIAGNOSIS-CANARY-PHI
    1003,Carol Demo,000-00-0003,4111-1111-1111-1113,MRN-CORTEXSIM-003,DIAGNOSIS-CANARY-PHI
  EOT
  tags = {
    CortexSimAISPMFinding = "training-data-pii-content"
    Sensitivity           = "regulated"
  }
}

# Insecure model serialization — covers TC-AISP-04 (Static Risk Analysis).
# Object is a placeholder; real `pickle.dumps` payload not needed — Cortex
# AI-SPM detects on the `.pkl` extension + bucket-level AI workload tag.
resource "aws_s3_object" "pickled_model" {
  bucket  = aws_s3_bucket.training_data.id
  key     = "models/legacy_model.pkl"
  content = "PLACEHOLDER_PICKLE_PAYLOAD_DO_NOT_DESERIALIZE"
  tags = {
    CortexSimAISPMFinding = "insecure-model-serialization-pickle"
    ModelFormat           = "pickle"
  }
}

# =========================================================================
# SAGEMAKER — managed model endpoint with overprivileged execution role
# Covers TC-AISP-01 (inventory), TC-AISP-02 (model security).
# =========================================================================

resource "aws_iam_role" "sagemaker_overprivileged" {
  name = "${local.name_prefix}-aispm-sagemaker-overprivileged"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "sagemaker.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = merge(local.common_tags, {
    Name                  = "${local.name_prefix}-aispm-sagemaker-overprivileged"
    CortexSimAISPMFinding = "overprivileged-ml-execution-role"
  })
}

# Intentionally over-permissive: managed AdministratorAccess on an ML role
# is a real-world AISP-02 finding ("access control review for AI workloads").
resource "aws_iam_role_policy_attachment" "sagemaker_admin" {
  role       = aws_iam_role.sagemaker_overprivileged.name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}

resource "aws_sagemaker_model" "poisoning_candidate" {
  name               = "${local.name_prefix}-aispm-poisoning-candidate-${random_id.suffix.hex}"
  execution_role_arn = aws_iam_role.sagemaker_overprivileged.arn

  primary_container {
    # Smallest publicly-available SageMaker built-in image — just enough for
    # the model resource to exist for inventory. No actual model artifact
    # behavior; the moat is "Cortex finds it in inventory", not "it works".
    image = "382416733822.dkr.ecr.${var.region}.amazonaws.com/linear-learner:1"
  }

  tags = merge(local.common_tags, {
    Name                  = "${local.name_prefix}-aispm-poisoning-candidate"
    CortexSimAISPMFinding = "ml-model-with-overprivileged-role"
    AIWorkload            = "true"
  })
}

resource "aws_sagemaker_endpoint_configuration" "canary" {
  name = "${local.name_prefix}-aispm-canary-cfg-${random_id.suffix.hex}"

  production_variants {
    variant_name           = "AllTraffic"
    model_name             = aws_sagemaker_model.poisoning_candidate.name
    initial_instance_count = 1
    instance_type          = "ml.t2.medium"
  }

  tags = merge(local.common_tags, {
    Name                  = "${local.name_prefix}-aispm-canary-cfg"
    CortexSimAISPMFinding = "endpoint-config-without-encryption"
  })
}

resource "aws_sagemaker_endpoint" "canary" {
  name                 = "${local.name_prefix}-aispm-sagemaker-endpoint-${random_id.suffix.hex}"
  endpoint_config_name = aws_sagemaker_endpoint_configuration.canary.name

  tags = merge(local.common_tags, {
    Name                  = "${local.name_prefix}-aispm-sagemaker-endpoint"
    CortexSimAISPMFinding = "managed-ai-endpoint"
    AIWorkload            = "true"
  })
}

# =========================================================================
# LAMBDA → OpenAI proxy with hardcoded API key (canary)
# Covers TC-AISP-01 (third-party AI integration discovery)
#         TC-AISP-04 (hardcoded credentials in ML pipeline)
# =========================================================================

resource "aws_iam_role" "lambda_openai" {
  name = "${local.name_prefix}-aispm-openai-proxy-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "lambda_basic" {
  role       = aws_iam_role.lambda_openai.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# Tiny Python proxy. Hardcoded "API key" is a planted canary; it is NOT
# a real OpenAI credential. Pattern `sk-DEMO-CORTEXSIM-AISP-04-PLANTED` is
# regex-detectable by any AI-SPM static-analysis engine.
data "archive_file" "lambda_payload" {
  type        = "zip"
  output_path = "${path.module}/lambda_openai_payload.zip"

  source {
    filename = "index.py"
    content  = <<-PY
      """OpenAI proxy Lambda — canary fixture for TC-AISP-04 static analysis."""
      import json
      import os

      # PLANTED CANARY — not a real OpenAI key.
      # AI-SPM static analyzer should flag this as `hardcoded_credentials_in_ml_pipeline`.
      OPENAI_API_KEY = "sk-DEMO-CORTEXSIM-AISP-04-PLANTED"
      OPENAI_MODEL   = "gpt-4-turbo"
      OPENAI_URL     = "https://api.openai.com/v1/chat/completions"

      def lambda_handler(event, context):
          # Intentionally no input validation — AISP-04 `unvalidated_model_inputs`
          prompt = event.get("prompt", "")
          return {"statusCode": 200, "body": json.dumps({"prompt": prompt, "model": OPENAI_MODEL})}
    PY
  }
}

resource "aws_lambda_function" "openai_proxy" {
  function_name    = "${local.name_prefix}-aispm-openai-proxy"
  role             = aws_iam_role.lambda_openai.arn
  handler          = "index.lambda_handler"
  runtime          = "python3.11"
  filename         = data.archive_file.lambda_payload.output_path
  source_code_hash = data.archive_file.lambda_payload.output_base64sha256
  timeout          = 30

  environment {
    variables = {
      # Same canary key duplicated into env vars — AI-SPM should detect both
      # the in-code and in-env occurrences. Two findings, one Lambda.
      OPENAI_API_KEY = "sk-DEMO-CORTEXSIM-AISP-04-PLANTED"
      AI_PROVIDER    = "openai"
    }
  }

  tags = merge(local.common_tags, {
    Name                  = "${local.name_prefix}-aispm-openai-proxy"
    CortexSimAISPMFinding = "third-party-ai-integration"
    AIWorkload            = "true"
    AIProvider            = "openai"
  })
}

# =========================================================================
# SHADOW AI — opt-in g4dn.xlarge with Ollama LLM container
# Covers TC-AISP-01 (the headline "we found shadow AI you didn't know existed").
# Default off to keep POV cost contained.
# =========================================================================

data "aws_ami" "ubuntu_22" {
  count       = var.enable_shadow_gpu ? 1 : 0
  most_recent = true
  owners      = ["099720109477"]
  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }
}

resource "aws_security_group" "shadow_gpu" {
  count       = var.enable_shadow_gpu ? 1 : 0
  name        = "${local.name_prefix}-aispm-shadow-gpu-sg"
  description = "Shadow GPU LLM host — egress only, no inbound"
  vpc_id      = var.vpc_id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = local.common_tags
}

resource "aws_instance" "shadow_gpu_llm" {
  count                       = var.enable_shadow_gpu ? 1 : 0
  ami                         = data.aws_ami.ubuntu_22[0].id
  instance_type               = "g4dn.xlarge"
  subnet_id                   = var.subnet_id
  vpc_security_group_ids      = [aws_security_group.shadow_gpu[0].id]
  associate_public_ip_address = true

  user_data = <<-EOT
    #!/bin/bash
    set -e
    curl -fsSL https://ollama.com/install.sh | sh || true
    ollama pull llama3:8b || true
    nohup ollama serve > /var/log/ollama.log 2>&1 &
  EOT

  tags = merge(local.common_tags, {
    Name                  = "${local.name_prefix}-aispm-shadow-gpu-llm"
    CortexSimAISPMFinding = "shadow-ai-on-unmanaged-gpu"
    AIWorkload            = "true"
    AIProvider            = "self-hosted-ollama"
  })
}
