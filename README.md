# Invoice Processing Automation

A cloud-based invoice processing system using AWS Textract, S3, DynamoDB, and Flask.
This is the final project from the cloud computing class in ESADE originally.
Then, I modified the environment from using EC2 instance to serverless RENDER.

https://invoice-processing-automation.onrender.com

If you want to use, please tell me. I'll give the password to enter.

## Architecture

```
PDF Upload (Flask/app.py)
    → S3 Bucket
        → Lambda Trigger (lambda_function.py)
            → AWS Textract (analyze_expense)
                → DynamoDB (stores extracted invoice data)
Flask Dashboard ← reads from DynamoDB
```

## Components

| File | Role |
|------|------|
| `app.py` | Flask web app — upload PDFs, view dashboard, REST API |
| `lambda_function.py` | AWS Lambda — triggered by S3, extracts invoice data via Textract, saves to DynamoDB |

## Setup

### 1. Clone the repository

```bash
git clone <repo-url>
cd Invoice_Processing_Automation
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure environment variables

```bash
cp .env.example .env
# Edit .env with your actual values
```

Required variables:

| Variable | Description |
|----------|-------------|
| `AWS_REGION` | AWS region (e.g. `us-east-1`) |
| `S3_BUCKET_NAME` | S3 bucket name for PDF uploads |
| `DYNAMO_TABLE` | DynamoDB table name |
| `FLASK_SECRET_KEY` | Flask session secret key |

### 4. AWS Credentials

Configure AWS credentials via `~/.aws/credentials` or environment variables:

```bash
export AWS_ACCESS_KEY_ID=your-access-key
export AWS_SECRET_ACCESS_KEY=your-secret-key
```

### 5. Run the Flask app

```bash
python app.py
```

The app will be available at `http://localhost:5000`.

## DynamoDB Table Schema

| Key | Type | Description |
|-----|------|-------------|
| `InvoiceId` | String (Partition Key) | `bucket/object-key` |
| `UploadDate` | String (Sort Key) | UTC ISO timestamp |
| `Summary` | String (JSON) | Textract extracted fields |
| `Items` | String (JSON) | Line item details |
| `PaymentStatus` | Boolean | Payment status |
| `PdfUrl` | String | Presigned S3 URL (24h) |

## Lambda Deployment

Deploy `lambda_function.py` to AWS Lambda with:
- **Trigger**: S3 `ObjectCreated` event on your bucket
- **Environment variable**: `DYNAMO_TABLE` = your DynamoDB table name
- **IAM permissions**: `s3:GetObject`, `textract:AnalyzeExpense`, `dynamodb:PutItem`
