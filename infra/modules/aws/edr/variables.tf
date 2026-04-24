variable "project_name" {
  description = "Lowercase-hyphen project name used as resource prefix"
  type        = string
}

variable "vpc_id" {
  description = "ID of the VPC from the base module"
  type        = string
}

variable "private_subnet_ids" {
  description = "List of private subnet IDs from the base module"
  type        = list(string)
}

variable "jumpbox_security_group_id" {
  description = "SG of the jumpbox — target hosts allow SSH from here"
  type        = string
}

variable "ssh_key_name" {
  description = "Name of the AWS keypair to attach to target hosts"
  type        = string
}

variable "target_count" {
  description = "Number of target EDR hosts"
  type        = number
  default     = 2
}

variable "target_size" {
  description = "EC2 instance type for target hosts"
  type        = string
  default     = "t3.small"
}

variable "tags" {
  description = "Additional tags applied to all resources"
  type        = map(string)
  default     = {}
}
