# AWS Provider
provider "aws" {
  region = var.aws_region
}

# EKS Cluster
resource "aws_eks_cluster" "main" {
  name     = var.cluster_name
  role_arn = aws_iam_role.eks_cluster.arn
  version  = var.k8s_version

  vpc_config {
    subnet_ids = var.subnet_ids
  }

  depends_on = [
    aws_iam_role_policy_attachment.eks_cluster_policy
  ]
}

# EKS Node Group
resource "aws_eks_node_group" "portal" {
  cluster_name    = aws_eks_cluster.main.name
  node_group_name = "portal-nodes"
  node_role_arn   = aws_iam_role.eks_nodes.arn
  subnet_ids      = var.subnet_ids

  scaling_config {
    desired_size = 2
    max_size     = 4
    min_size     = 1
  }

  instance_types = ["t3.medium"]

  depends_on = [
    aws_iam_role_policy_attachment.eks_worker_node_policy,
    aws_iam_role_policy_attachment.eks_cni_policy,
    aws_iam_role_policy_attachment.eks_container_registry_policy
  ]
}

# IAM Roles
resource "aws_iam_role" "eks_cluster" {
  name = "${var.cluster_name}-cluster-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "eks.amazonaws.com"
      }
    }]
  })
}

resource "aws_iam_role" "eks_nodes" {
  name = "${var.cluster_name}-nodes-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action = "sts:AssumeRole"
      Effect = "Allow"
      Principal = {
        Service = "ec2.amazonaws.com"
      }
    }]
  })
}

# IAM Policies
resource "aws_iam_role_policy_attachment" "eks_cluster_policy" {
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
  role       = aws_iam_role.eks_cluster.name
}

resource "aws_iam_role_policy_attachment" "eks_worker_node_policy" {
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy"
  role       = aws_iam_role.eks_nodes.name
}

resource "aws_iam_role_policy_attachment" "eks_cni_policy" {
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy"
  role       = aws_iam_role.eks_nodes.name
}

resource "aws_iam_role_policy_attachment" "eks_container_registry_policy" {
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
  role       = aws_iam_role.eks_nodes.name
}

# ECR Repository for Portal
resource "aws_ecr_repository" "portal" {
  name = "engineering-flow-platform-portal"
}

# ECR Repository for Agent
resource "aws_ecr_repository" "agent" {
  name = "engineering-flow-platform"
}

# ============================================
# EFS with Access Point
# ============================================

# EFS File System
resource "aws_efs_file_system" "portal" {
  creation_token = "portal-efs"
  encrypted     = true
  tags = {
    Name = "${var.cluster_name}-efs"
  }
}

# EFS Access Point for Portal
resource "aws_efs_access_point" "portal" {
  file_system_id = aws_efs_file_system.portal.id

  posix_user {
    gid = 1000
    uid = 1000
  }

  root_directory {
    path = "/portal"

    creation_info {
      owner_gid   = 1000
      owner_uid   = 1000
      permissions = "700"
    }
  }

  tags = {
    Name = "${var.cluster_name}-portal-ap"
  }
}

# EFS Access Point for Agents
resource "aws_efs_access_point" "agents" {
  file_system_id = aws_efs_file_system.portal.id

  posix_user {
    gid = 1000
    uid = 1000
  }

  root_directory {
    path = "/agents"

    creation_info {
      owner_gid   = 1000
      owner_uid   = 1000
      permissions = "700"
    }
  }

  tags = {
    Name = "${var.cluster_name}-agents-ap"
  }
}

# Security Group for EFS
resource "aws_security_group" "efs" {
  name        = "${var.cluster_name}-efs-sg"
  description = "Security group for EFS"
  vpc_id      = var.vpc_id

  ingress {
    from_port   = 2049
    to_port     = 2049
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
  }

  tags = {
    Name = "${var.cluster_name}-efs-sg"
  }
}
