variable "region" {
  description = "The AWS region. Singapore is the nearest to Malaysia."
  type        = string
  default     = "ap-southeast-1"
}

variable "name" {
  description = "The Lambda function name, which also names its role and its log group."
  type        = string
  default     = "finplat-scorer"
}

variable "public" {
  description = "true gives the Function URL no authentication, so a reviewer can call it."
  type        = bool
  default     = true
}
