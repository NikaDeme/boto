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


def validate_subnet_count(value):
    n = int(value)
    if n < 1:
        raise argparse.ArgumentTypeError("Number of subnets must be at least 1")
    if n > 200:
        raise argparse.ArgumentTypeError("Number of subnets cannot exceed 200 (AWS limit)")
    return n


def tag(resource_id, name):
    ec2.create_tags(Resources=[resource_id], Tags=[{"Key": "Name", "Value": name}])


def generate_subnet_cidrs(vpc_cidr, n):

    vpc_network = ipaddress.IPv4Network(vpc_cidr, strict=False)

    halves = list(vpc_network.subnets(prefixlen_diff=1))
    public_half = halves[0]
    private_half = halves[1]

    import math
    bits_needed = math.ceil(math.log2(n)) if n > 1 else 0

    public_subnets = list(public_half.subnets(prefixlen_diff=bits_needed))[:n]
    private_subnets = list(private_half.subnets(prefixlen_diff=bits_needed))[:n]

    return [str(s) for s in public_subnets], [str(s) for s in private_subnets]


def create(args):
    print(f"Creating infrastructure with {args.num_subnets} public and {args.num_subnets} private subnet(s)...")

    public_cidrs, private_cidrs = generate_subnet_cidrs(args.vpc_cidr, args.num_subnets)

    print(f"  Public  subnet CIDRs: {public_cidrs}")
    print(f"  Private subnet CIDRs: {private_cidrs}")

    # VPC
    vpc_id = ec2.create_vpc(CidrBlock=args.vpc_cidr)["Vpc"]["VpcId"]
    tag(vpc_id, args.vpc_name)
    print(f"\n  VPC created: {vpc_id}")

    # Internet Gateway (public subnets need this to reach the internet)
    igw_id = ec2.create_internet_gateway()["InternetGateway"]["InternetGatewayId"]
    tag(igw_id, f"{args.vpc_name}-igw")
    ec2.attach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)
    print(f"  IGW created & attached: {igw_id}")

    # Public Route Table — one shared table for all public subnets

    pub_rt_id = ec2.create_route_table(VpcId=vpc_id)["RouteTable"]["RouteTableId"]
    ec2.create_route(RouteTableId=pub_rt_id, DestinationCidrBlock="0.0.0.0/0", GatewayId=igw_id)
    tag(pub_rt_id, f"{args.vpc_name}-public-rt")
    print(f"  Public Route Table created: {pub_rt_id}")

    # Private Route Table — one shared table for all private subnets

    priv_rt_id = ec2.create_route_table(VpcId=vpc_id)["RouteTable"]["RouteTableId"]
    tag(priv_rt_id, f"{args.vpc_name}-private-rt")
    print(f"  Private Route Table created: {priv_rt_id}")

    # Create N public subnets and associate them with the public route table
    print(f"\n  Creating {args.num_subnets} public subnet(s)...")
    for i, cidr in enumerate(public_cidrs):
        sub_id = ec2.create_subnet(VpcId=vpc_id, CidrBlock=cidr)["Subnet"]["SubnetId"]
        tag(sub_id, f"{args.vpc_name}-public-{i + 1}")
        ec2.associate_route_table(RouteTableId=pub_rt_id, SubnetId=sub_id)

        ec2.modify_subnet_attribute(SubnetId=sub_id, MapPublicIpOnLaunch={"Value": True})
        print(f"    Public Subnet {i + 1}: {sub_id} ({cidr})")

    # Create N private subnets and associate them with the private route table
    print(f"\n  Creating {args.num_subnets} private subnet(s)...")
    for i, cidr in enumerate(private_cidrs):
        sub_id = ec2.create_subnet(VpcId=vpc_id, CidrBlock=cidr)["Subnet"]["SubnetId"]
        tag(sub_id, f"{args.vpc_name}-private-{i + 1}")
        ec2.associate_route_table(RouteTableId=priv_rt_id, SubnetId=sub_id)
        print(f"    Private Subnet {i + 1}: {sub_id} ({cidr})")

    print("\nAll resources created successfully.")


def destroy(args):
    vpcs = ec2.describe_vpcs(Filters=[{"Name": "tag:Name", "Values": [args.vpc_name]}])["Vpcs"]
    if not vpcs:
        print(f"No VPC found with name '{args.vpc_name}'")
        sys.exit(1)

    vpc_id = vpcs[0]["VpcId"]
    print(f"Destroying infrastructure for VPC: {vpc_id}")

    # 1. Detach and delete IGW
    for igw in ec2.describe_internet_gateways(
        Filters=[{"Name": "attachment.vpc-id", "Values": [vpc_id]}]
    )["InternetGateways"]:
        igw_id = igw["InternetGatewayId"]
        ec2.detach_internet_gateway(InternetGatewayId=igw_id, VpcId=vpc_id)
        ec2.delete_internet_gateway(InternetGatewayId=igw_id)
        print(f"  IGW deleted: {igw_id}")

    # 2. Delete all subnets
    for subnet in ec2.describe_subnets(
        Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
    )["Subnets"]:
        sub_id = subnet["SubnetId"]
        ec2.delete_subnet(SubnetId=sub_id)
        print(f"  Subnet deleted: {sub_id}")

    # 3. Delete non-main route tables
    for rt in ec2.describe_route_tables(
        Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
    )["RouteTables"]:
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
    parser = argparse.ArgumentParser(description="AWS VPC Manager with multiple subnets")
    parser.add_argument("--region", default="us-east-1", help="AWS region (default: us-east-1)")
    sub = parser.add_subparsers(dest="action", required=True)

    # create subcommand
    c = sub.add_parser("create", help="Create a VPC with public and private subnets")
    c.add_argument("--vpc-name", required=True, help="Name tag for the VPC")
    c.add_argument("--vpc-cidr", required=True, type=validate_cidr, help="VPC CIDR block, e.g. 10.0.0.0/16")
    c.add_argument(
        "--num-subnets",
        required=True,
        type=validate_subnet_count,
        help="Number of public AND private subnets to create (1-200)",
    )

    # destroy subcommand
    d = sub.add_parser("destroy", help="Destroy a VPC and all its resources")
    d.add_argument("--vpc-name", required=True, help="Name tag of the VPC to destroy")

    args = parser.parse_args()

    global ec2
    ec2 = boto3.client("ec2", region_name=args.region)

    if args.action == "create":
        create(args)
    elif args.action == "destroy":
        destroy(args)


if __name__ == "__main__":
    main()
