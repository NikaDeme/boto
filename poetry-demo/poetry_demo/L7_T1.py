import argparse
import ipaddress
import sys
import boto3

ec2 = None


def validate_cidr(cidr):
    try:
        ipaddress.IPv4Network(cidr, strict=False)
        return cidr
    except ValueError:
        raise argparse.ArgumentTypeError(f"Invalid CIDR: {cidr}")


def tag(resource_id, name):
    ec2.create_tags(Resources=[resource_id], Tags=[{"Key": "Name", "Value": name}])


def create(args):
    print("Creating infrastructure...")

    # VPC
    vpc_id = ec2.create_vpc(CidrBlock=args.vpc_cidr)["Vpc"]["VpcId"]
    tag(vpc_id, args.vpc_name)
    print(f"  VPC created: {vpc_id}")

    # Internet Gateway
    igw_id = ec2.create_internet_gateway()["InternetGateway"]["InternetGatewayId"]
    tag(igw_id, f"{args.vpc_name}-igw")
    ec2.attach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)
    print(f"  IGW created & attached: {igw_id}")

    # Public Subnet
    pub_sub_id = ec2.create_subnet(VpcId=vpc_id, CidrBlock=args.public_cidr)["Subnet"]["SubnetId"]
    tag(pub_sub_id, f"{args.vpc_name}-public")
    print(f"  Public Subnet created: {pub_sub_id}")

    # Private Subnet
    priv_sub_id = ec2.create_subnet(VpcId=vpc_id, CidrBlock=args.private_cidr)["Subnet"]["SubnetId"]
    tag(priv_sub_id, f"{args.vpc_name}-private")
    print(f"  Private Subnet created: {priv_sub_id}")

    # Public Route Table (with internet route)
    pub_rt_id = ec2.create_route_table(VpcId=vpc_id)["RouteTable"]["RouteTableId"]
    ec2.create_route(RouteTableId=pub_rt_id, DestinationCidrBlock="0.0.0.0/0", GatewayId=igw_id)
    ec2.associate_route_table(RouteTableId=pub_rt_id, SubnetId=pub_sub_id)
    print(f"  Public Route Table created: {pub_rt_id}")

    # Private Route Table
    priv_rt_id = ec2.create_route_table(VpcId=vpc_id)["RouteTable"]["RouteTableId"]
    ec2.associate_route_table(RouteTableId=priv_rt_id, SubnetId=priv_sub_id)
    print(f"  Private Route Table created: {priv_rt_id}")

    print("\nResources created successfully.")


def destroy(args):
    # Find VPC by name
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "tag:Name", "Values": [args.vpc_name]}])["Vpcs"]
    if not vpcs:
        print(f"No VPC found with name '{args.vpc_name}'")
        sys.exit(1)

    vpc_id = vpcs[0]["VpcId"]
    print(f"Destroying infrastructure for VPC: {vpc_id}")

    # 1. Detach and delete IGW
    for igw in ec2.describe_internet_gateways(Filters=[{"Name": "attachment.vpc-id", "Values": [vpc_id]}])["InternetGateways"]:
        igw_id = igw["InternetGatewayId"]
        ec2.detach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)
        ec2.delete_internet_gateway(InternetGatewayId=igw_id)
        print(f"  IGW deleted: {igw_id}")

    # 2. Delete Subnets
    for subnet in ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])["Subnets"]:
        sub_id = subnet["SubnetId"]
        ec2.delete_subnet(SubnetId=sub_id)
        print(f"  Subnet deleted: {sub_id}")

    # 3. Delete Route Tables (skip the main one)
    for rt in ec2.describe_route_tables(Filters=[{"Name": "vpc-id", "Values": [vpc_id]}])["RouteTables"]:
        if any(a.get("Main") for a in rt["Associations"]):
            continue
        rt_id = rt["RouteTableId"]
        for assoc in rt["Associations"]:
            ec2.disassociate_route_table(AssociationId=assoc["RouteTableAssociationId"])
        ec2.delete_route_table(RouteTableId=rt_id)
        print(f"  Route Table deleted: {rt_id}")

    # 4. Delete VPC
    ec2.delete_vpc(VpcId=vpc_id)
    print(f"  VPC deleted: {vpc_id}")

    print("\nDone! All resources destroyed.")


def main():
    parser = argparse.ArgumentParser(description="Simple AWS VPC Manager")
    parser.add_argument("--region", default="us-east-1")
    sub = parser.add_subparsers(dest="action", required=True)

    # create
    c = sub.add_parser("create")
    c.add_argument("--vpc-name", required=True)
    c.add_argument("--vpc-cidr", required=True, type=validate_cidr)
    c.add_argument("--public-cidr", required=True, type=validate_cidr)
    c.add_argument("--private-cidr", required=True, type=validate_cidr)

    # destroy
    d = sub.add_parser("destroy")
    d.add_argument("--vpc-name", required=True)

    args = parser.parse_args()

    global ec2
    ec2 = boto3.client("ec2", region_name=args.region)

    if args.action == "create":
        create(args)
    elif args.action == "destroy":
        destroy(args)


if __name__ == "__main__":
    main()