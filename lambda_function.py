import boto3
import json
import uuid
import os
import traceback
from datetime import datetime

dynamodb = boto3.resource('dynamodb')
s3 = boto3.client('s3')
table = dynamodb.Table(os.environ['DYNAMO_TABLE']) 

def lambda_handler(event, context):
    bucket = event['Records'][0]['s3']['bucket']['name']
    document = event['Records'][0]['s3']['object']['key']

    textract = boto3.client('textract')
    response = textract.analyze_expense(
        Document={'S3Object': {'Bucket': bucket, 'Name': document}}
    )

    invoice_id = f"{bucket}/{document}"

    try:
        summary = {}
        for field in response["ExpenseDocuments"][0]["SummaryFields"]:
            field_type = field.get("Type", {}).get("Text")
            value = field.get("ValueDetection", {}).get("Text")
            if field_type and value:
                summary[field_type] = value
        items = []
        for group in response["ExpenseDocuments"][0].get("LineItemGroups", []):
            for line_item in group.get("LineItems", []):
                item = {
                    "Description": line_item.get("LineItemExpenseFields", [])[0].get("ValueDetection", {}).get("Text"),
                    "Quantity": next((f["ValueDetection"]["Text"] for f in line_item["LineItemExpenseFields"] if f["Type"]["Text"] == "QUANTITY"), None),
                    "UnitPrice": next((f["ValueDetection"]["Text"] for f in line_item["LineItemExpenseFields"] if f["Type"]["Text"] == "UNIT_PRICE"), None),
                    "Price": next((f["ValueDetection"]["Text"] for f in line_item["LineItemExpenseFields"] if f["Type"]["Text"] == "PRICE"), None),
                    }
                items.append(item)
        pdf_url = s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket, 'Key': document},
            ExpiresIn=86400)

        table.put_item(Item={
            'InvoiceId': invoice_id,
            'UploadDate': datetime.utcnow().isoformat(),
            'Bucket': bucket,
            'Document': document,
            'Summary': json.dumps(summary, ensure_ascii=False),
            'Items': json.dumps(items, ensure_ascii=False),
            'PaymentStatus':False,
            'PdfUrl': pdf_url
            })
        print("✅ DynamoDB put_item response:", response)
        print(f"✅ Saved item: {json.dumps(item, indent=2, ensure_ascii=False)}")


    except Exception as e:
        print(f"❌ DynamoDB put_item failed: {e}")
        print(traceback.format_exc()) 

        raise
    
    return {
        'statusCode': 200,
        'body': f"Saved invoice {invoice_id} to DynamoDB"
    }
