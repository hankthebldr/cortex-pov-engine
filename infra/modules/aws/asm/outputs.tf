output "exposed_host_public_ip" {
  description = "Public IP of the exposed multi-service EC2 instance"
  value       = aws_instance.exposed.public_ip
}

output "exposed_host_public_dns" {
  description = "Public DNS of the exposed host (for ASM domain-based discovery)"
  value       = aws_instance.exposed.public_dns
}

output "exposed_ports" {
  description = "Ports exposed on the public IP for Cortex ASM discovery"
  value = [
    "22 (SSH standard - closed)",
    "80 (HTTP with directory listing)",
    "443 (HTTPS with weak TLS + RSA-1024 cert)",
    "2222 (SSH non-standard with password auth)",
    "6379 (Redis no-auth)",
    "9001 (gocortexbrokenbank)",
    "9200 (fake Elasticsearch banner)",
  ]
}

output "public_website_url" {
  description = "Public S3 website URL (static hosting, open policy)"
  value       = "http://${aws_s3_bucket.website.id}.s3-website-${var.region}.amazonaws.com"
}

output "findings_summary" {
  description = "Summary of intentional ASM findings planted"
  value = {
    exposed_ec2            = aws_instance.exposed.public_ip
    public_s3_website      = aws_s3_bucket.website.id
    ssh_non_standard_port  = "2222"
    nginx_directory_listing = "http://${aws_instance.exposed.public_ip}/"
    weak_tls_cert           = "https://${aws_instance.exposed.public_ip}/"
    vulnerable_app          = "http://${aws_instance.exposed.public_ip}:9001/"
    exposed_redis           = "redis://${aws_instance.exposed.public_ip}:6379/"
    fake_elasticsearch      = "http://${aws_instance.exposed.public_ip}:9200/"
  }
}
