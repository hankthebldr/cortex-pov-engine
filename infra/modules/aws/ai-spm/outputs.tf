output "sagemaker_endpoint_name" {
  description = "SageMaker endpoint name (managed AI asset Cortex AI-SPM should discover)"
  value       = aws_sagemaker_endpoint.canary.name
}

output "sagemaker_endpoint_arn" {
  description = "SageMaker endpoint ARN — primary AISP-01 inventory target"
  value       = aws_sagemaker_endpoint.canary.arn
}

output "sagemaker_model_name" {
  description = "SageMaker model with overprivileged execution role (AISP-02 target)"
  value       = aws_sagemaker_model.poisoning_candidate.name
}

output "lambda_openai_proxy_arn" {
  description = "Lambda → OpenAI proxy with hardcoded canary key (AISP-04 target)"
  value       = aws_lambda_function.openai_proxy.arn
}

output "training_data_bucket" {
  description = "S3 bucket containing PII/PHI/PCI canary fixtures (AISP-05 target)"
  value       = aws_s3_bucket.training_data.id
}

output "pickled_model_object" {
  description = "S3 object key for the insecurely-serialized model (AISP-04 target)"
  value       = "${aws_s3_bucket.training_data.id}/${aws_s3_object.pickled_model.key}"
}

output "shadow_gpu_instance_id" {
  description = "Shadow GPU EC2 instance ID running self-hosted Ollama LLM (AISP-01 headline finding). Empty if enable_shadow_gpu=false."
  value       = var.enable_shadow_gpu ? aws_instance.shadow_gpu_llm[0].id : ""
}

output "shadow_gpu_enabled" {
  description = "Whether the opt-in shadow GPU instance was provisioned"
  value       = var.enable_shadow_gpu
}

output "findings_summary" {
  description = "Manifest of intentional AI-SPM findings planted. Compare against Cortex AI-SPM's discovered inventory to compute Asset Discovery Coverage % (the AISP-01 KPI)."
  value = {
    managed_ai_endpoint            = aws_sagemaker_endpoint.canary.name
    overprivileged_ml_role         = aws_iam_role.sagemaker_overprivileged.arn
    pickled_model_artifact         = "${aws_s3_bucket.training_data.id}/${aws_s3_object.pickled_model.key}"
    third_party_ai_integration     = aws_lambda_function.openai_proxy.function_name
    hardcoded_credentials_in_code  = "sk-DEMO-CORTEXSIM-AISP-04-PLANTED (in Lambda code + env)"
    training_data_with_regulated   = aws_s3_bucket.training_data.id
    shadow_ai_on_unmanaged_gpu     = var.enable_shadow_gpu ? aws_instance.shadow_gpu_llm[0].id : "<<not provisioned — set enable_shadow_gpu=true>>"
    total_assets_planted           = var.enable_shadow_gpu ? 7 : 6
  }
}
