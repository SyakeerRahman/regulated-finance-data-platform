# Brief

## The problem

Nine job postings for data platform roles in regulated finance ask for Kubernetes, MLflow,
AWS Lambda, CI/CD, and Delta Lake. No public repository in the portfolio shows these tools.
This project shows them together in one working fraud-scoring platform.

## The reader

A hiring manager or an engineer who reviews the portfolio. The reader has 5 minutes. The reader
must see each tool do real work, not appear only in a list.

## Done when

One command sequence does these steps on a new machine, with no step done by hand:

1. Airflow loads transactions into Delta Lake tables (bronze, silver, gold).
2. Airflow trains a fraud model and registers it in MLflow.
3. A Lambda function scores one transaction with the registered model.
4. The stack runs on a local Kubernetes cluster, and GitHub Actions tests each push.

## Scope

| In | Out |
|---|---|
| Airflow, Delta Lake, MLflow, AWS Lambda, Kubernetes, Terraform, GitHub Actions | Java, Greenplum, Jira |

The card also lists Java, Greenplum, and Jira. They are out of scope because they close no gap on
the card and they add time.

## Plan

| Weekend | Work | Proves |
|---|---|---|
| 1 | Synthetic transactions, then an Airflow DAG that writes bronze, silver, and gold Delta tables | Airflow, Delta Lake |
| 2 | A fraud model trained from the gold table and registered in MLflow. A Lambda scores one transaction | MLflow, AWS Lambda |
| 3 | The stack on kind with Helm. Terraform for the Lambda. GitHub Actions for tests and deploy | Kubernetes, CI/CD |

## Source

The build card "Regulated Finance Data Platform with MLflow, Airflow, and Kubernetes" in
resume-builder (build id `cmsoooga3000184v466azywjm`). After the build, mark the card as built
with this repository.
