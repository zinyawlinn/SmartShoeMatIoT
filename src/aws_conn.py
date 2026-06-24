# aws_conn.py
import os
import inspect
import json
from datetime import datetime, timezone
from awscrt import mqtt
from awsiot import mqtt_connection_builder

# -----------------------------------------------------------------------------
# AWS IoT Configuration (Region: ap-southeast-2)
# -----------------------------------------------------------------------------
script_dir = os.path.dirname(os.path.abspath(inspect.getfile(inspect.currentframe())))

# === AWS IoT Core endpoint (from IoT Core → Settings → Device data endpoint) ===
ENDPOINT  = os.getenv(
    "AWS_IOT_ENDPOINT",
    "ayuy4cnd89dxf-ats.iot.ap-southeast-2.amazonaws.com"   # ✅ correct for your account ayuy4cnd89dxf-ats.iot.ap-southeast-2.amazonaws.com
)

# === Client ID (unique name for this Pi) ===
CLIENT_ID = os.getenv("AWS_IOT_CLIENT_ID", "shoemat-pi01")

# === MQTT topic used by your IoT Rule/Lambda ===
TOPIC_PUB = os.getenv("AWS_IOT_TOPIC_PUB", "iotdata")       # ✅ must match Rule: SELECT * FROM 'iotdata'

# === Certificate paths (all files in the same folder) ===
PATH_CERT = os.path.join(script_dir, "certificate.pem.crt")
PATH_KEY  = os.path.join(script_dir, "private.pem.key")
PATH_ROOT = os.path.join(script_dir, "AmazonRootCA1.pem")

# -----------------------------------------------------------------------------
# Connect to AWS IoT Core
# -----------------------------------------------------------------------------
def _connect():
    print(f"Connecting to AWS IoT Core at {ENDPOINT} as {CLIENT_ID} ...")
    conn = mqtt_connection_builder.mtls_from_path(
        endpoint=ENDPOINT,
        cert_filepath=PATH_CERT,
        pri_key_filepath=PATH_KEY,
        ca_filepath=PATH_ROOT,
        client_id=CLIENT_ID,
        clean_session=True,
        keep_alive_secs=30,
    )
    conn.connect().result()
    print("✅ Connected to AWS IoT Core")
    return conn

mqtt_connection = _connect()

# -----------------------------------------------------------------------------
# Helper functions
# -----------------------------------------------------------------------------
def utc_now_iso():
    """Return current UTC timestamp in ISO 8601 format with microseconds (Z)."""
    return datetime.now(timezone.utc).isoformat()

def publish_row(row: dict):
    """
    Publish a JSON payload matching columns in 'smartshoemat.shoe_checks_all'.

    Required keys:
        id, ts_utc, device_id, stage, status,
        precheck_session_id, check_id,
        rain_hit, moist_hit, rain_raw, moisture_raw,
        pressure_max, rain_thr, moist_thr
    """
    payload = json.dumps(row)
    mqtt_connection.publish(
        topic=TOPIC_PUB,
        payload=payload,
        qos=mqtt.QoS.AT_LEAST_ONCE,
    )
    print("📤 Published shoe_checks_all row to topic", TOPIC_PUB)
    print(payload)

