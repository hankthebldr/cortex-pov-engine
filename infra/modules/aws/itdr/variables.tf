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
  description = "SG of the jumpbox — ITDR hosts allow RDP/WinRM from here"
  type        = string
}

variable "ad_domain_name" {
  description = "Fully qualified Active Directory domain name (e.g. cortexsim.local)"
  type        = string
  default     = "cortexsim.local"
}

variable "ad_netbios_name" {
  description = "NetBIOS name for the AD domain (<= 15 chars, uppercase)"
  type        = string
  default     = "CORTEXSIM"
}

variable "ad_admin_password" {
  description = "Password for the Domain Admin account. Stored in SSM SecureString. If empty, a random 20-char password is generated."
  type        = string
  default     = ""
  sensitive   = true
}

variable "dc_instance_type" {
  description = "EC2 instance type for the domain controller"
  type        = string
  default     = "t3.large"
}

variable "workstation_instance_type" {
  description = "EC2 instance type for the domain-joined workstation"
  type        = string
  default     = "t3.medium"
}

variable "workstation_count" {
  description = "Number of domain-joined workstations to provision"
  type        = number
  default     = 1
}

variable "tags" {
  description = "Additional tags applied to all resources"
  type        = map(string)
  default     = {}
}
