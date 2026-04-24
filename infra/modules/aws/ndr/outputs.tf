output "collector_private_ip" {
  description = "Private IP of the NGFW log collector (forward NGFW logs here on :8080 HTTP or :514 syslog)"
  value       = aws_instance.collector.private_ip
}

output "collector_log_group" {
  description = "CloudWatch log group receiving VPC Flow Logs"
  value       = aws_cloudwatch_log_group.flow.name
}

output "attack_endpoint_public_ip" {
  description = "Public IP of the DMZ attack endpoint (source of simulated C2/exfil traffic)"
  value       = aws_instance.attack_endpoint.public_ip
}

output "attack_endpoint_instance_id" {
  description = "EC2 instance ID of the attack endpoint"
  value       = aws_instance.attack_endpoint.id
}

output "stitching_pattern" {
  description = "Which stitching pattern the module was generated for"
  value       = var.stitching_pattern
}

output "stitching_guidance" {
  description = "Next-step guidance for wiring the chosen NGFW pattern"
  value = var.stitching_pattern == "marketplace_vmseries" ? (
    "Launch VM-Series from AWS Marketplace. Use the VPC and public subnet of this environment. Configure PAN-OS HTTP log forwarding to http://${aws_instance.collector.private_ip}:8080/."
  ) : var.stitching_pattern == "external_ngfw_forward" ? (
    "Configure your existing NGFW to forward syslog/HTTP logs to ${aws_instance.collector.private_ip}:8080 (HTTP) or :514 (syslog). Ensure your NGFW network can route to this collector."
  ) : (
    "Suricata mode: run Suricata on the attack endpoint in IDS mode with eve.json forwarded to http://${aws_instance.collector.private_ip}:8080/. See README for exact setup."
  )
}
