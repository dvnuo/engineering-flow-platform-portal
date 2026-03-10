output "cluster_name" {
  description = "EKS Cluster name"
  value       = aws_eks_cluster.main.name
}

output "cluster_endpoint" {
  description = "EKS Cluster endpoint"
  value       = aws_eks_cluster.main.endpoint
}

output "portal_ecr_url" {
  description = "Portal ECR repository URL"
  value       = aws_ecr_repository.portal.repository_url
}

output "agent_ecr_url" {
  description = "Agent ECR repository URL"
  value       = aws_ecr_repository.agent.repository_url
}

output "efs_file_system_id" {
  description = "EFS File System ID"
  value       = aws_efs_file_system.portal.id
}

output "efs_access_point_portal_id" {
  description = "EFS Access Point ID for Portal"
  value       = aws_efs_access_point.portal.id
}

output "efs_access_point_agents_id" {
  description = "EFS Access Point ID for Agents"
  value       = aws_efs_access_point.agents.id
}

output "efs_security_group_id" {
  description = "EFS Security Group ID"
  value       = aws_security_group.efs.id
}
