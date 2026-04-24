output "dc_private_ip" {
  description = "Private IP of the domain controller"
  value       = aws_instance.dc.private_ip
}

output "dc_instance_id" {
  description = "EC2 instance ID of the domain controller"
  value       = aws_instance.dc.id
}

output "workstation_private_ips" {
  description = "Private IPs of domain-joined workstations"
  value       = aws_instance.workstation[*].private_ip
}

output "ad_domain_name" {
  description = "Fully-qualified AD domain name"
  value       = var.ad_domain_name
}

output "ad_netbios_name" {
  description = "NetBIOS domain name"
  value       = var.ad_netbios_name
}

output "ad_admin_password_ssm_path" {
  description = "SSM SecureString parameter path for the Domain Admin password"
  value       = aws_ssm_parameter.ad_admin_password.name
}
