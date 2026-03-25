#!/usr/bin/env python3
"""
app.py - Flask app for invoice dashboard + S3 upload + DynamoDB items.

Designed to work with:
  Table: textract-cloudcomputing-team9
  Partition key: InvoiceId (String)
  Sort key: UploadDate (String)

Features:
 - DynamoDB support (uses boto3)
 - Local JSON fallback (local_items.json) when DynamoDB unavailable
 - Decimal conversion for numbers before Dynamo writes
 - S3 upload & listing (safe when no credentials)
 - API: /api/items (GET, POST), /api/items/<id> (GET, PUT, DELETE)
 - /api/demo_create to create demo invoices
 - Frontend-friendly id: "InvoiceId__UploadDate"
"""

import os
import re
import json
import logging
from dotenv import load_dotenv

load_dotenv()
from datetime import datetime, timezone
from decimal import Decimal
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, session
from werkzeug.utils import secure_filename
import boto3
from botocore.exceptions import ClientError, NoCredentialsError
from boto3.dynamodb.conditions import Key

# --------------------
# Configuration
# --------------------
S3_BUCKET = os.environ.get("S3_BUCKET_NAME")
S3_FOLDER = os.environ.get("S3_FOLDER", "uploads/")
DYNAMO_TABLE = os.environ.get("DYNAMO_TABLE")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
LOCAL_DB_FILE = os.environ.get("LOCAL_DB_FILE", "local_items.json")

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET_KEY")

# AWS clients (may raise NoCredentialsError later if no role/keys)
s3_client = boto3.client("s3", region_name=AWS_REGION)
dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("invoicer")

# --------------------
# Helpers
# --------------------
def get_table():
    if not DYNAMO_TABLE:
        raise RuntimeError("DYNAMO_TABLE not configured")
    return dynamodb.Table(DYNAMO_TABLE)


def is_dynamo_available():
    try:
        t = get_table()
        _ = t.table_status  # will raise if table doesn't exist or no creds
        return True
    except Exception as e:
        logger.debug("DynamoDB unavailable: %s", e)
        return False


def current_ts():
    return int(datetime.now(timezone.utc).timestamp())


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def safe_num(v):
    try:
        return float(v)
    except Exception:
        return 0.0

from datetime import datetime
from datetime import datetime

from datetime import datetime

def parse_date(val):
    if not val:
        return None

    formats = [
        "%Y-%m-%d",        # 2025-12-04
        "%Y-%m-%dT%H:%M:%S.%f",  # 2025-12-04T19:57:58.346174
        "%Y-%m-%dT%H:%M:%S",     # 2025-12-04T19:57:58
        "%d %B %Y",        # 04 December 2025
        "%d %b %Y",        # 10 Jan 2026
        "%d%b%Y",          # 10Jan2026
        "%d.%m.%Y",        # 20.02.2026
        "%d/%m/%Y",        # 20/02/2026
    ]

    for fmt in formats:
        try:
            d = datetime.strptime(val.strip(), fmt)
            return d.date().isoformat()
        except Exception:
            continue

    try:
        d = datetime.fromisoformat(val.strip())
        return d.date().isoformat()
    except Exception:
        return None

import re

def parse_total(val):
    if not val:
        return 0.0

    s = str(val).strip()
    s = re.sub(r"(EUR|€)", "", s, flags=re.IGNORECASE).strip()
    s = s.replace(",", "")

    try:
        return float(s)
    except ValueError:
        return 0.0

# Decimal conversion helper for Dynamo
def convert_numbers_for_dynamo(obj):
    """
    Recursively convert float/int to Decimal for DynamoDB compatibility.
    Operates in place and returns the object.
    """
    if isinstance(obj, dict):
        for k, v in list(obj.items()):
            if v is None:
                continue
            if isinstance(v, (float, int)) and not isinstance(v, Decimal):
                try:
                    obj[k] = Decimal(str(v))
                except Exception:
                    if isinstance(v, int):
                        obj[k] = Decimal(v)
            elif isinstance(v, dict):
                convert_numbers_for_dynamo(v)
            elif isinstance(v, list):
                new_list = []
                for item in v:
                    if isinstance(item, (float, int)) and not isinstance(item, Decimal):
                        try:
                            new_list.append(Decimal(str(item)))
                        except Exception:
                            new_list.append(Decimal(item) if isinstance(item, int) else item)
                    elif isinstance(item, (dict, list)):
                        convert_numbers_for_dynamo(item)
                        new_list.append(item)
                    else:
                        new_list.append(item)
                obj[k] = new_list
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, (float, int)) and not isinstance(v, Decimal):
                try:
                    obj[i] = Decimal(str(v))
                except Exception:
                    if isinstance(v, int):
                        obj[i] = Decimal(v)
            elif isinstance(v, (dict, list)):
                convert_numbers_for_dynamo(v)
    return obj


# Local fallback storage
def load_local_items():
    try:
        if not os.path.exists(LOCAL_DB_FILE):
            return []
        with open(LOCAL_DB_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
            return data if isinstance(data, list) else []
    except Exception as e:
        logger.exception("Failed to load local items: %s", e)
        return []


def save_local_items(items):
    try:
        tmp = LOCAL_DB_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(items, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, LOCAL_DB_FILE)
        return True
    except Exception as e:
        logger.exception("Failed to save local items: %s", e)
        return False


def find_local_item(items, invoice_id, upload_date):
    for it in items:
        if str(it.get("InvoiceId")) == str(invoice_id) and str(it.get("UploadDate")) == str(upload_date):
            return it
    return None


# Frontend id helpers
def make_frontend_id(item):
    inv = item.get("InvoiceId") or item.get("InvoiceID") or item.get("invoiceId")
    up = item.get("UploadDate") or item.get("uploadDate") or item.get("ts") or now_iso()
    return f"{inv}__{up}"


def split_frontend_id(fid):
    if "__" in fid:
        parts = fid.split("__", 1)
        return parts[0], parts[1]
    return fid, None


def find_latest_for_invoice(invoice_id):
    try:
        table = get_table()
        # query partition, descending by sort key to get latest
        resp = table.query(
            KeyConditionExpression=Key("InvoiceId").eq(invoice_id),
            ScanIndexForward=False,
            Limit=1
        )
        items = resp.get("Items", []) or []
        if items:
            return items[0], items[0].get("UploadDate")
    except Exception as e:
        logger.debug("Query latest failed: %s", e)
    return None, None


# --------------------
# Auth helpers
# --------------------
APP_PASSWORD = os.environ.get("APP_PASSWORD")

def login_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("authenticated"):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


# --------------------
# Routes - pages
# --------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if request.form.get("password") == APP_PASSWORD:
            session["authenticated"] = True
            return redirect(url_for("home"))
        error = "パスワードが違います"
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/", methods=["GET"])
@login_required
def home():
    return render_template("index.html", page="home")


@app.route("/upload", methods=["GET", "POST"])
@login_required
def upload():
    if request.method == "POST":
        if "file" not in request.files:
            flash("No file part")
            return redirect(request.url)
        file = request.files["file"]
        if file.filename == "":
            flash("No selected file")
            return redirect(request.url)
        filename = secure_filename(file.filename)
        if not filename.lower().endswith(".pdf"):
            flash("Only PDF allowed")
            return redirect(request.url)
        key = os.path.join(S3_FOLDER, filename)
        try:
            s3_client.upload_fileobj(file, S3_BUCKET, key)
            flash(f"Uploaded: {filename}")
        except NoCredentialsError:
            logger.exception("S3 upload failed - no credentials")
            flash("Upload failed: no AWS credentials available (S3).")
        except ClientError as e:
            logger.exception("S3 upload failed")
            flash(f"Upload failed: {e}")
        return redirect(url_for("upload"))
    return render_template("index.html", page="upload")


@app.route("/dashboard", methods=["GET"])
@login_required
def dashboard():
    # list S3 files
    files = []
    try:
        resp = s3_client.list_objects_v2(Bucket=S3_BUCKET, Prefix=S3_FOLDER)
        for obj in resp.get("Contents", []) if resp else []:
            key = obj.get("Key")
            if key and key.lower().endswith(".pdf"):
                files.append({
                    "key": key,
                    "name": os.path.basename(key),
                    "size": obj.get("Size", 0),
                    "last_modified": obj.get("LastModified").isoformat() if obj.get("LastModified") else ""
                })
    except NoCredentialsError:
        logger.debug("S3 list: no credentials")
    except Exception as e:
        logger.exception("Error listing S3 objects: %s", e)

    # load items from Dynamo or local fallback
    items = []
    if is_dynamo_available():
        try:
            table = get_table()
            resp = table.scan()
            items = resp.get("Items", []) or []
        except Exception as e:
            logger.debug("Dynamo scan failed: %s", e)
            items = load_local_items()
    else:
        items = load_local_items()

    # normalize and compute metrics
    for it in items:
        it["id"] = make_frontend_id(it)

    total = len(items)
    paid_count = sum(1 for it in items if str(it.get("paid", False)).lower() in ["true", "1", "y", "yes"] or it.get("paid") is True)
    unpaid_count = total - paid_count

    amounts = []
    for it in items:
        try:
            amounts.append(float(str(it.get("amount", 0)).replace(",", "")))
        except Exception:
            pass

    avg_amount = round(sum(amounts) / len(amounts), 2) if amounts else 0.0
    total_value = round(sum(amounts), 2) if amounts else 0.0

    unpaid_items = [it for it in items if not (str(it.get("paid", False)).lower() in ["true", "1", "y", "yes"] or it.get("paid") is True)]
    try:
        top_unpaid = sorted(unpaid_items, key=lambda x: safe_num(x.get("amount", 0)), reverse=True)[:5]
    except Exception:
        top_unpaid = unpaid_items[:5]

    insights = []
    if total == 0:
        insights.append("No invoices found yet — upload PDFs or add a demo invoice.")
    else:
        unpaid_ratio = round((unpaid_count / total) * 100, 1) if total else 0.0
        insights.append(f"Unpaid ratio: {unpaid_ratio}% ({unpaid_count} of {total}).")
        if unpaid_ratio > 40:
            insights.append("High unpaid ratio — consider follow-up communications.")
        if avg_amount > 1000:
            insights.append(f"Average invoice value is €{avg_amount} — monitor large invoices.")
        if top_unpaid:
            top_list = ", ".join([f"{it.get('InvoiceId') or it.get('id') or '—'} (€{safe_num(it.get('amount',0))})" for it in top_unpaid[:3]])
            insights.append(f"Top unpaid: {top_list}.")

    metrics = {
        "total": total,
        "paid": paid_count,
        "unpaid": unpaid_count,
        "avg_amount": avg_amount,
        "total_value": total_value
    }

    return render_template("index.html", page="dashboard", files=files, items=items, insights=insights, metrics=metrics)


# --------------------
# API - items (GET, POST)
# --------------------
import json

@app.route("/api/items", methods=["GET"])
def api_items():
    if is_dynamo_available():
        table = get_table()
        resp = table.scan()
        items = resp.get("Items", []) or []
    else:
        items = load_local_items()

    normalized = []
    for it in items:

        summary_raw = it.get("Summary", {})
        if isinstance(summary_raw, str):
            try:
                summary = json.loads(summary_raw)
            except Exception:
                summary = {}
        elif isinstance(summary_raw, dict):
            summary = summary_raw
        else:
            summary = {}
        items_raw = it.get("Items", [])
        if isinstance(items_raw, str):
            try:
                items_list = json.loads(items_raw)
            except Exception:
                items_list = []
        elif isinstance(items_raw, list):
            items_list = items_raw
        else:
            items_list = []

        normalized.append({
            "id": make_frontend_id(it),
            "UploadDate": parse_date(it.get("UploadDate")),
            "Vendor": summary.get("VENDOR_NAME"),
            "Items": [{
                "description": item.get("Description"),
                "quantity": item.get("Quantity"),
                "unitPrice": item.get("UnitPrice"),
                "price": item.get("Price")
                } for item in items_list],
            "Total": parse_total(summary.get("TOTAL")),
            "PaymentStatus": it.get("PaymentStatus"),
            "DueDate": parse_date(summary.get("DUE_DATE")),
            "Document": it.get("Document"),
            "Bucket": it.get("Bucket"),
            "PdfUrl": it.get("PdfUrl")
            })

    return jsonify({"ok": True, "items": normalized}), 200

# --------------------
# API - item detail (GET, PUT, DELETE)
# --------------------
import json

@app.route("/api/items/<string:item_id>", methods=["GET", "PUT", "DELETE"])
def api_item_detail(item_id):
    invoice_id, upload_date = split_frontend_id(item_id)
    key = {"InvoiceId": invoice_id, "UploadDate": upload_date}

    if request.method == "GET":
        # GET処理
        resp = get_table().get_item(Key=key)
        it = resp.get("Item")
        if not it:
            return jsonify({"ok": False, "error": "not_found"}), 404

        summary_raw = it.get("Summary", {})
        if isinstance(summary_raw, str):
            try:
                summary = json.loads(summary_raw)
            except Exception:
                summary = {}
        elif isinstance(summary_raw, dict):
            summary = summary_raw
        else:
            summary = {}

        frontend_item = {
            "id": make_frontend_id(it),
            "UploadDate": it.get("UploadDate"),
            "Vendor": summary.get("VENDOR_NAME"),
            "Items": it.get("Items", []),
            "Total": parse_total(summary.get("TOTAL")),
            "PaymentStatus": it.get("PaymentStatus", False),
            "DueDate": summary.get("DUE_DATE"),
            "Summary": summary,
            "Document": it.get("Document"),
            "Bucket": it.get("Bucket")
        }
        return jsonify({"ok": True, "item": frontend_item}), 200

    # PUT - update attributes
    if request.method == "PUT":
        data = request.get_json(force=True, silent=True) or {}
        if not data:
            return jsonify({"ok": False, "error": "no_data"}), 400

        if is_dynamo_available():
            try:
                expr = []
                values = {}
                names = {}
                for k, v in data.items():
                    expr.append(f"#_{k} = :{k}")
                    names[f"#_{k}"] = k
                    # convert numeric types to Decimal
                    if isinstance(v, (float, int)) and not isinstance(v, Decimal):
                        try:
                            values[f":{k}"] = Decimal(str(v))
                        except Exception:
                            if isinstance(v, int):
                                values[f":{k}"] = Decimal(v)
                            else:
                                values[f":{k}"] = v
                    else:
                        values[f":{k}"] = v
                update_expr = "SET " + ", ".join(expr)
                resp = get_table().update_item(
                    Key=key,
                    UpdateExpression=update_expr,
                    ExpressionAttributeNames=names,
                    ExpressionAttributeValues=values,
                    ReturnValues="ALL_NEW"
                )
                updated = resp.get("Attributes", {})
                updated["id"] = make_frontend_id(updated)
                return jsonify({"ok": True, "updated": updated}), 200
            except Exception as e:
                logger.exception("Dynamo update failed: %s", e)
                return jsonify({"ok": False, "error": str(e)}), 500
        else:
            items_local = load_local_items()
            it = find_local_item(items_local, invoice_id, upload_date)
            if not it:
                return jsonify({"ok": False, "error": "not_found"}), 404
            for k, v in data.items():
                it[k] = v
            saved = save_local_items(items_local)
            it["id"] = make_frontend_id(it)
            return jsonify({"ok": True, "updated": it, "saved": saved}), 200 if saved else 500

    # DELETE
    if request.method == "DELETE":
        if is_dynamo_available():
            try:
                get_table().delete_item(Key=key)
                return jsonify({"ok": True}), 200
            except Exception as e:
                logger.exception("Dynamo delete failed: %s", e)
                return jsonify({"ok": False, "error": str(e)}), 500
        else:
            items_local = load_local_items()
            new_items = [x for x in items_local if not (str(x.get("InvoiceId")) == str(invoice_id) and str(x.get("UploadDate")) == str(upload_date))]
            saved = save_local_items(new_items)
            return jsonify({"ok": True, "saved": saved}), 200 if saved else 500


# --------------------
# Health & demo endpoints
# --------------------
@app.route("/api/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "dynamo": is_dynamo_available()})


@app.route("/api/demo_create", methods=["POST"])
def demo_create():
    payload = request.get_json(force=True, silent=True) or {}
    demo_invoice = payload.get("InvoiceId") or f"DEMO-{current_ts()}"
    demo_upload = payload.get("UploadDate") or now_iso()
    demo = {
        "InvoiceId": demo_invoice,
        "UploadDate": demo_upload,
        "filename": payload.get("filename", "demo-invoice.pdf"),
        "amount": payload.get("amount", 19.99),
        "paid": payload.get("paid", False),
        "ts": current_ts()
    }

    # try Dynamo
    if is_dynamo_available():
        try:
            convert_numbers_for_dynamo(demo)
            get_table().put_item(Item=demo)
            demo["id"] = make_frontend_id(demo)
            return jsonify({"ok": True, "item": demo, "saved": True}), 201
        except Exception as e:
            logger.exception("dynamo demo put failed, falling back: %s", e)

    # fallback local
    items = load_local_items()
    items.append(demo)
    saved = save_local_items(items)
    demo["id"] = make_frontend_id(demo)
    return jsonify({"ok": True, "item": demo, "saved": saved, "fallback": True}), 201 if saved else 500


# --------------------
# Run server
# --------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
