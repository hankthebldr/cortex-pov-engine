variable "project_name" {
  description = "Lowercase-hyphen project name used as resource prefix"
  type        = string
}

variable "vpc_id" {
  description = "ID of the VPC from the base module (shadow GPU EC2 lands here)"
  type        = string
}

variable "subnet_id" {
  description = "Public subnet ID from the base module (shadow GPU EC2 needs egress)"
  type        = string
}

variable "region" {
  description = "AWS region (for globally-unique S3 bucket naming + Bedrock region scoping)"
  type        = string
}

variable "enable_shadow_gpu" {
  description = "Provision the g4dn.xlarge shadow-AI EC2 instance (~$0.50/hr). Defaults off for cost-sensitive POVs; the managed-AI assets alone are enough to pass TC-AISP-01."
  type        = bool
  default     = false
}

variable "tags" {
  description = "Additional tags applied to all resources"
  type        = map(string)
  default     = {}
}
