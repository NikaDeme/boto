import sys, time, argparse
import boto3
from botocore.exceptions import ClientError

# RDS instance class -> memory GiB
MEMORY = {
    "db.t3.micro": 1,   "db.t3.small": 2,   "db.t3.medium": 4,  "db.t3.large": 8,
    "db.t3.xlarge": 16, "db.t3.2xlarge": 32,
    "db.m5.large": 8,   "db.m5.xlarge": 16,  "db.m5.2xlarge": 32, "db.m5.4xlarge": 64,
    "db.m6i.large": 8,  "db.m6i.xlarge": 16, "db.m6i.2xlarge": 32, "db.m6i.4xlarge": 64,
    "db.r5.large": 16,  "db.r5.xlarge": 32,  "db.r5.2xlarge": 64, "db.r5.4xlarge": 128,
    "db.r5.8xlarge": 256,
    "db.r6i.large": 16, "db.r6i.xlarge": 32, "db.r6i.2xlarge": 64, "db.r6i.4xlarge": 128,
    "db.r6g.large": 16, "db.r6g.xlarge": 32, "db.r6g.2xlarge": 64, "db.r6g.4xlarge": 128,
    "db.x2g.large": 32, "db.x2g.xlarge": 64, "db.x2g.2xlarge": 128,
}

# Sorted list for upgrade lookup
CLASSES = sorted(MEMORY.items(), key=lambda x: x[1])

def next_class(current_class):
    """Return the smallest class with >= 1.25x the current memory."""
    current_mem = MEMORY.get(current_class)
    if not current_mem:
        print(f"[ERROR] Unknown instance class: {current_class}")
        sys.exit(1)
    target = current_mem * 1.25
    for cls, mem in CLASSES:
        if mem >= target:
            return cls, current_mem, mem
    print(f"[ERROR] No larger class available above {current_class}")
    sys.exit(1)


parser = argparse.ArgumentParser(description="Manage RDS and DynamoDB")
parser.add_argument("--rds-id",   required=True, help="RDS instance identifier")
parser.add_argument("--region",   default="us-east-1")
args = parser.parse_args()

rds     = boto3.client("rds",     region_name=args.region)
dynamo  = boto3.client("dynamodb",region_name=args.region)

# ── 1. Print all DynamoDB tables ─────────────────────────────────────────────
print("\n[1/3] DynamoDB tables in", args.region)
tables = []
paginator = dynamo.get_paginator("list_tables")
for page in paginator.paginate():
    tables.extend(page["TableNames"])

if tables:
    for t in tables:
        print(f"  • {t}")
else:
    print("  (no tables found)")

# ── 2. Increase RDS memory by 25% ────────────────────────────────────────────
print(f"\n[2/3] Upgrading RDS instance: {args.rds_id}")
info = rds.describe_db_instances(DBInstanceIdentifier=args.rds_id)["DBInstances"][0]
current_class = info["DBInstanceClass"]
status        = info["DBInstanceStatus"]
print(f"  Current class : {current_class}  ({MEMORY.get(current_class, '?')} GiB RAM)  [{status}]")

new_class, old_mem, new_mem = next_class(current_class)
print(f"  New class     : {new_class}  ({new_mem} GiB RAM)  [+{new_mem - old_mem} GiB / +{round((new_mem/old_mem - 1)*100)}%]")

rds.modify_db_instance(
    DBInstanceIdentifier=args.rds_id,
    DBInstanceClass=new_class,
    ApplyImmediately=True,          # apply now, not at next maintenance window
)
print("  Modification submitted (ApplyImmediately=True).")
print("  Waiting for instance to be available again ", end="", flush=True)

deadline = time.time() + 1800
while time.time() < deadline:
    s = rds.describe_db_instances(DBInstanceIdentifier=args.rds_id)["DBInstances"][0]["DBInstanceStatus"]
    if s == "available":
        print(" done.")
        break
    print(".", end="", flush=True)
    time.sleep(20)
else:
    print("\n[WARN] Timed out waiting — check AWS console.")

# ── 3. Manual snapshot ────────────────────────────────────────────────────────
print(f"\n[3/3] Creating manual snapshot for: {args.rds_id}")
snap_id = f"{args.rds_id}-manual-{int(time.time())}"
rds.create_db_snapshot(
    DBInstanceIdentifier=args.rds_id,
    DBSnapshotIdentifier=snap_id,
    Tags=[{"Key": "Type", "Value": "manual-backup"}],
)
print(f"  Snapshot ID : {snap_id}")
print(f"  Waiting for snapshot ", end="", flush=True)

deadline = time.time() + 1800
while time.time() < deadline:
    snap = rds.describe_db_snapshots(DBSnapshotIdentifier=snap_id)["DBSnapshots"][0]
    pct  = snap.get("PercentProgress", 0)
    if snap["Status"] == "available":
        print(f" done (100%).")
        break
    print(f"\r  Waiting for snapshot [{pct}%] ", end="", flush=True)
    time.sleep(15)
else:
    print("\n[WARN] Snapshot not ready yet — check AWS console.")

# ── Summary ───────────────────────────────────────────────────────────────────
print(f"""
{'='*52}
DONE
{'='*52}
DynamoDB tables : {len(tables)}
RDS upgrade     : {current_class} → {new_class}  ({old_mem} → {new_mem} GiB)
Snapshot        : {snap_id}
{'='*52}
""")