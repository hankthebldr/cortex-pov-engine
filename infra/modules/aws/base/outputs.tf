output "vpc_id" {
  description = "ID of the CortexSim VPC"
  value       = aws_vpc.main.id
}

output "public_subnet_ids" {
  description = "IDs of the public subnets"
  value       = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  description = "IDs of the private subnets"
  value       = aws_subnet.private[*].id
}

output "jumpbox_public_ip" {
  description = "Public IP of the jumpbox"
  value       = aws_instance.jumpbox.public_ip
}

output "jumpbox_private_ip" {
  description = "Private IP of the jumpbox"
  value       = aws_instance.jumpbox.private_ip
}

output "jumpbox_security_group_id" {
  description = "Security group ID attached to the jumpbox (reused by other modules)"
  value       = aws_security_group.jumpbox.id
}

output "ssh_key_name" {
  description = "AWS keypair name for the jumpbox"
  value       = aws_key_pair.jumpbox.key_name
}

output "ssh_private_key_ssm_path" {
  description = "SSM parameter path storing the private SSH key"
  value       = aws_ssm_parameter.jumpbox_private_key.name
}

output "region" {
  description = "Deployment region"
  value       = var.region
}

output "project_name" {
  description = "Project name used as resource prefix"
  value       = var.project_name
}
