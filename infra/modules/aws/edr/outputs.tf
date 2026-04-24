output "target_private_ips" {
  description = "Private IPs of EDR target hosts (reachable from jumpbox)"
  value       = aws_instance.target[*].private_ip
}

output "target_instance_ids" {
  description = "EC2 instance IDs of EDR targets"
  value       = aws_instance.target[*].id
}

output "target_security_group_id" {
  description = "Security group ID for target hosts"
  value       = aws_security_group.target.id
}
