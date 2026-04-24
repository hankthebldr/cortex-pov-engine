variable "project_name" {
  description = "Lowercase-hyphen project name used as resource prefix"
  type        = string
}

variable "region" {
  description = "AWS region for all resources"
  type        = string
}

variable "dc_ssh_cidr" {
  description = "CIDR allowed SSH access to the jumpbox"
  type        = string
}

variable "jumpbox_size" {
  description = "EC2 instance type for the jumpbox"
  type        = string
  default     = "t3.medium"
}

variable "tags" {
  description = "Additional tags applied to all resources"
  type        = map(string)
  default     = {}
}

variable "content_modules" {
  description = "List of module names whose content.yml should be processed on jumpbox boot"
  type        = list(string)
  default     = ["base"]
}
