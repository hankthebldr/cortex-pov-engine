variable "project_name" {
  description = "Lowercase-hyphen project name used as resource prefix"
  type        = string
}

variable "vpc_id" {
  description = "ID of the VPC from the base module"
  type        = string
}

variable "public_subnet_ids" {
  description = "List of public subnet IDs from the base module (used for DMZ attack endpoint)"
  type        = list(string)
}

variable "private_subnet_ids" {
  description = "List of private subnet IDs from the base module (used for internal targets + log collector)"
  type        = list(string)
}

variable "jumpbox_security_group_id" {
  description = "SG of the jumpbox — NDR hosts allow SSH from here"
  type        = string
}

variable "ssh_key_name" {
  description = "AWS keypair name from the base module"
  type        = string
}

variable "dc_ssh_cidr" {
  description = "CIDR allowed to reach the log collector HTTP endpoint (from DC's workstation)"
  type        = string
}

variable "stitching_pattern" {
  description = "NGFW deployment pattern: 'marketplace_vmseries' | 'external_ngfw_forward' | 'suricata_lab'"
  type        = string
  default     = "external_ngfw_forward"
  validation {
    condition     = contains(["marketplace_vmseries", "external_ngfw_forward", "suricata_lab"], var.stitching_pattern)
    error_message = "stitching_pattern must be one of: marketplace_vmseries, external_ngfw_forward, suricata_lab."
  }
}

variable "collector_instance_type" {
  description = "Instance type for the log collector (runs ackbarx + HTTP ingestion)"
  type        = string
  default     = "t3.small"
}

variable "attack_endpoint_instance_type" {
  description = "Instance type for the DMZ attack endpoint"
  type        = string
  default     = "t3.small"
}

variable "tags" {
  description = "Additional tags applied to all resources"
  type        = map(string)
  default     = {}
}
