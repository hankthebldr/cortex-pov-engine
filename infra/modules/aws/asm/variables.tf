variable "project_name" {
  description = "Lowercase-hyphen project name used as resource prefix"
  type        = string
}

variable "vpc_id" {
  description = "ID of the VPC from the base module"
  type        = string
}

variable "public_subnet_ids" {
  description = "List of public subnet IDs from the base module"
  type        = list(string)
}

variable "region" {
  description = "AWS region (for S3 website endpoint)"
  type        = string
}

variable "exposed_instance_type" {
  description = "EC2 instance type for the exposed endpoint"
  type        = string
  default     = "t3.small"
}

variable "tags" {
  description = "Additional tags applied to all resources"
  type        = map(string)
  default     = {}
}
