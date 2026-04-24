variable "project_name" {
  description = "Lowercase-hyphen project name used as resource prefix"
  type        = string
}

variable "vpc_id" {
  description = "ID of the VPC from the base module"
  type        = string
}

variable "region" {
  description = "AWS region (for globally-unique S3 bucket naming)"
  type        = string
}

variable "tags" {
  description = "Additional tags applied to all resources"
  type        = map(string)
  default     = {}
}
