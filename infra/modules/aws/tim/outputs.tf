output "tim_host_public_ip" {
  description = "Public IP of the TIM host (TAXII + fake C2)"
  value       = aws_instance.tim.public_ip
}

output "taxii_endpoint_url" {
  description = "TAXII 2.1 server URL for XSIAM TIM feed configuration"
  value       = "http://${aws_instance.tim.public_ip}:9000/taxii/"
}

output "fake_c2_url" {
  description = "Fake C2 endpoint - endpoint beacons to this URL should fire TIM IOC detections"
  value       = "http://${aws_instance.tim.public_ip}:8000/"
}

output "planted_ioc_domains" {
  description = "Planted DNS records inside the private zone (resolve to fake_c2_url)"
  value = [
    for name in ["c2-beacon", "exfil-drop", "payload-delivery", "dga-1a2b3c", "cryptominer-pool"] :
    "${name}.${aws_route53_zone.tim_private.name}"
  ]
}

output "private_zone_id" {
  description = "Route 53 private zone ID for the TIM test records"
  value       = aws_route53_zone.tim_private.id
}
