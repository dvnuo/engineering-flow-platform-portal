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