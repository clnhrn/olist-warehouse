# Locals shared across files. File-specific locals stay next to their resources.
locals {
  # Dev and prod share one project; resources fan out per env with for_each.
  envs = toset(["dev", "prod"])
}
