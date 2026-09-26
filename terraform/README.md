# Terraform

One Lambda, one role, one log group, one Function URL. Nothing else.

## Before the first apply

1. Set an AWS budget alarm at USD 1. Do this before anything else.
2. Turn on MFA for the root account, then stop using root.
3. Create an IAM user for Terraform and run `aws configure`.

## Apply

```bash
uv run python -m finplat.export lambda_fn/model.json
uv run python scripts/build_lambda.py
cd terraform
terraform init
terraform plan
terraform apply
```

`terraform output function_url` prints the address. Test it:

```bash
curl -s -X POST "$(terraform output -raw function_url)" -H 'content-type: application/json' -d '{
  "transaction": {
    "transaction_id": "20260926-0015837", "account_id": "ACC01518", "amount": 411.13,
    "ts": "2026-09-26T11:58:39+00:00", "country": "US", "channel": "online",
    "merchant_category": "fuel"
  },
  "prior_transactions": 5, "prior_mean": 14.49
}'
```

## Cost

| Item | Free tier | Cost here |
| ---- | --------- | --------- |
| Lambda requests | 1M each month, no expiry | RM 0 |
| Lambda compute | 400,000 GB-seconds each month | RM 0 |
| Function URL | Part of Lambda | RM 0 |
| CloudWatch logs | 5 GB each month | RM 0 at 14-day retention |

There is no API Gateway, no ECR and no S3 bucket, so there is nothing that starts to charge
after 12 months.

## Remove it

```bash
terraform destroy
```

## A new model

Export, rebuild, apply. Terraform sees a new `source_code_hash` and replaces the code only.

```bash
uv run python -m finplat.export lambda_fn/model.json
uv run python scripts/build_lambda.py
cd terraform && terraform apply
```
