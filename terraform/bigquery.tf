# Datasets only. Bronze tables are created by the ingestion job from contracts/,
# so each schema lives in one place.
locals {
  layers = ["bronze", "silver", "gold"]

  datasets = {
    for pair in setproduct(tolist(local.envs), local.layers) :
    (pair[0] == "prod" ? pair[1] : "dev_${pair[1]}") => {
      env   = pair[0]
      layer = pair[1]
    }
  }
}

resource "google_bigquery_dataset" "layer" {
  for_each = local.datasets

  dataset_id                 = each.key
  location                   = var.region
  description                = "${title(each.value.layer)} layer (${each.value.env})."
  delete_contents_on_destroy = false

  labels = {
    env   = each.value.env
    layer = each.value.layer
  }

  depends_on = [google_project_service.apis]
}
