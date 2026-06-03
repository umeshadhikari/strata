variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "eu-west-1"
}

variable "customer_id" {
  description = "Customer identifier prefix for all resources"
  type        = string
}

variable "data_mart_jdbc_url" {
  description = "JDBC URL for the data mart (Oracle or PostgreSQL)"
  type        = string
}

variable "data_mart_subnet_id" {
  description = "Subnet ID Glue uses to reach the data mart"
  type        = string
}

variable "data_mart_security_group_ids" {
  description = "Security groups Glue uses to reach the data mart"
  type        = list(string)
}

variable "data_mart_availability_zone" {
  description = "AZ for the Glue connection"
  type        = string
}
