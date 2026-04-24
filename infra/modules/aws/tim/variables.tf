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

variable "jumpbox_security_group_id" {
  description = "SG of the jumpbox — TIM host allows SSH from here"
  type        = string
}

variable "ssh_key_name" {
  description = "AWS keypair name from the base module"
  type        = string
}

variable "dc_ssh_cidr" {
  description = "CIDR allowed to reach the TAXII server (for XSIAM ingest configuration)"
  type        = string
}

variable "tim_instance_type" {
  description = "EC2 instance type for the TIM host (mocktaxii + DNS)"
  type        = string
  default     = "t3.small"
}

variable "tags" {
  description = "Additional tags applied to all resources"
  type        = map(string)
  default     = {}
}
