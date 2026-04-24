output "public_bucket_name" {
  description = "S3 bucket with public-read ACL"
  value       = aws_s3_bucket.public.id
}

output "public_bucket_url" {
  description = "HTTP URL that should be publicly reachable (proves CSPM finding severity)"
  value       = "http://${aws_s3_bucket.public.id}.s3.${var.region}.amazonaws.com/README.txt"
}

output "ssh_open_sg_id" {
  description = "Security group ID with SSH open to 0.0.0.0/0"
  value       = aws_security_group.ssh_open_world.id
}

output "db_open_sg_id" {
  description = "Security group ID with DB ports open to 0.0.0.0/0"
  value       = aws_security_group.db_open_world.id
}

output "admin_role_arn" {
  description = "IAM role ARN with AdministratorAccess policy"
  value       = aws_iam_role.admin_role.arn
}

output "overprivileged_user_name" {
  description = "IAM user with iam:* inline policy"
  value       = aws_iam_user.overprivileged.name
}

output "unencrypted_volume_id" {
  description = "Orphan unencrypted EBS volume ID"
  value       = aws_ebs_volume.unencrypted.id
}

output "findings_summary" {
  description = "Summary of intentional CSPM findings planted"
  value = {
    s3_public_read         = aws_s3_bucket.public.id
    s3_versioning_disabled = aws_s3_bucket.unversioned.id
    s3_no_kms              = aws_s3_bucket.no_kms.id
    sg_ssh_open            = aws_security_group.ssh_open_world.id
    sg_db_open             = aws_security_group.db_open_world.id
    iam_role_admin         = aws_iam_role.admin_role.arn
    iam_user_wildcard      = aws_iam_user.overprivileged.name
    ebs_unencrypted        = aws_ebs_volume.unencrypted.id
    cloudtrail_weak        = aws_cloudtrail.weak.arn
  }
}
