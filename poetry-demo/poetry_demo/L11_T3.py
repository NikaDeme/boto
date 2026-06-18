from os import getenv
import random
import boto3

# ── Movie data ────────────────────────────────────────────────────────────────
MOVIES = [
    {"id": "1",  "title": "The Grand Budapest Hotel", "genre": "Comedy",          "year": "2014", "rating": "8.1"},
    {"id": "2",  "title": "La La Land",               "genre": "Romance",         "year": "2016", "rating": "8.0"},
    {"id": "3",  "title": "Mad Max: Fury Road",        "genre": "Action",          "year": "2015", "rating": "8.1"},
    {"id": "4",  "title": "Get Out",                   "genre": "Horror",          "year": "2017", "rating": "7.7"},
    {"id": "5",  "title": "Inception",                 "genre": "Science Fiction", "year": "2010", "rating": "8.8"},
    {"id": "6",  "title": "Knives Out",                "genre": "Mystery",         "year": "2019", "rating": "7.9"},
    {"id": "7",  "title": "The Shawshank Redemption",  "genre": "Drama",           "year": "1994", "rating": "9.3"},
    {"id": "8",  "title": "The Dark Knight",           "genre": "Thriller",        "year": "2008", "rating": "9.0"},
    {"id": "9",  "title": "Into the Wild",             "genre": "Adventure",       "year": "2007", "rating": "8.1"},
    {"id": "10", "title": "Bohemian Rhapsody",         "genre": "Biography",       "year": "2018", "rating": "7.9"},
    {"id": "11", "title": "Hereditary",                "genre": "Horror",          "year": "2018", "rating": "7.3"},
    {"id": "12", "title": "Interstellar",              "genre": "Science Fiction", "year": "2014", "rating": "8.6"},
    {"id": "13", "title": "Parasite",                  "genre": "Thriller",        "year": "2019", "rating": "8.5"},
    {"id": "14", "title": "The Notebook",              "genre": "Romance",         "year": "2004", "rating": "7.9"},
    {"id": "15", "title": "12 Years a Slave",          "genre": "Biography",       "year": "2013", "rating": "8.1"},
]

MOOD_GENRES = {
    "happy":        ["Comedy", "Romance", "Adventure"],
    "sad":          ["Drama", "Romance"],
    "excited":      ["Action", "Adventure", "Thriller"],
    "scary":        ["Horror", "Thriller"],
    "thoughtful":   ["Drama", "Science Fiction"],
    "intrigued":    ["Mystery", "Thriller"],
    "nostalgic":    ["Drama", "Romance"],
    "inspired":     ["Biography", "Drama"],
    "mysterious":   ["Thriller", "Mystery"],
    "action-packed":["Action", "Adventure", "Science Fiction"],
}

TABLE_NAME = "Movies"


# ── DynamoDB client ───────────────────────────────────────────────────────────
def get_client():
    return boto3.client(
        "dynamodb",
        aws_access_key_id=getenv("aws_access_key_id"),
        aws_secret_access_key=getenv("aws_secret_access_key"),
        aws_session_token=getenv("aws_session_token"),
        region_name=getenv("aws_region_name", "us-east-1"),
    )

def get_resource():
    return boto3.resource(
        "dynamodb",
        aws_access_key_id=getenv("aws_access_key_id"),
        aws_secret_access_key=getenv("aws_secret_access_key"),
        aws_session_token=getenv("aws_session_token"),
        region_name=getenv("aws_region_name", "us-east-1"),
    )


def create_table(dynamo_resource):
    """Create the Movies table. Equivalent to CREATE TABLE in MySQL."""
    table = dynamo_resource.create_table(
        TableName=TABLE_NAME,
        KeySchema=[{"AttributeName": "id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
        Tags=[{"Key": "Name", "Value": "Movies DynamoDB Table"}],
    )
    table.wait_until_exists()
    print(f"Table '{TABLE_NAME}' created and ready.")
    return table


def delete_table(dynamo_resource):
    table = dynamo_resource.Table(TABLE_NAME)
    table.delete()
    table.wait_until_not_exists()
    print(f"Table '{TABLE_NAME}' deleted.")


def seed_movies(dynamo_resource):
    table = dynamo_resource.Table(TABLE_NAME)
    with table.batch_writer() as batch:
        for movie in MOVIES:
            batch.put_item(Item=movie)
    print(f"Seeded {len(MOVIES)} movies into '{TABLE_NAME}'.")


def get_movie(dynamo_resource, movie_id):
    table = dynamo_resource.Table(TABLE_NAME)
    resp  = table.get_item(Key={"id": movie_id})
    item  = resp.get("Item")
    if item:
        print(f"Found: {item}")
    else:
        print(f"No movie with id={movie_id}")
    return item


def update_movie_rating(dynamo_resource, movie_id, new_rating):
    table = dynamo_resource.Table(TABLE_NAME)
    table.update_item(
        Key={"id": movie_id},
        UpdateExpression="SET rating = :r",
        ExpressionAttributeValues={":r": str(new_rating)},
    )
    print(f"Updated movie {movie_id} rating to {new_rating}.")


def delete_movie(dynamo_resource, movie_id):
    table = dynamo_resource.Table(TABLE_NAME)
    table.delete_item(Key={"id": movie_id})
    print(f"Deleted movie with id={movie_id}.")


def list_all_movies(dynamo_resource):
    table = dynamo_resource.Table(TABLE_NAME)
    items = table.scan()["Items"]
    print(f"\n{'─'*55}")
    print(f"{'ID':<4} {'Title':<35} {'Genre':<18} {'Year'} {'Rating'}")
    print(f"{'─'*55}")
    for m in sorted(items, key=lambda x: int(x["id"])):
        print(f"{m['id']:<4} {m['title']:<35} {m['genre']:<18} {m['year']}  {m['rating']}")
    print(f"{'─'*55}\n")
    return items


def print_table_info(dynamo_client):

    resp  = dynamo_client.describe_table(TableName=TABLE_NAME)
    table = resp["Table"]
    print(f"\nTable name  : {table['TableName']}")
    print(f"Status      : {table['TableStatus']}")
    print(f"Item count  : {table.get('ItemCount', 'N/A')}")
    print(f"ARN         : {table['TableArn']}")
    print(f"Billing     : {table.get('BillingModeSummary', {}).get('BillingMode', 'PROVISIONED')}")


def recommend_by_mood(dynamo_resource, mood):

    mood = mood.lower()
    genres = MOOD_GENRES.get(mood)
    if not genres:
        print(f"Unknown mood '{mood}'. Available: {', '.join(MOOD_GENRES)}")
        return []

    table = dynamo_resource.Table(TABLE_NAME)
    items = table.scan()["Items"]
    matches = [m for m in items if m.get("genre") in genres]

    if not matches:
        print(f"No movies found for mood '{mood}' (genres: {genres}).")
        return []

    picks = random.sample(matches, min(2, len(matches)))
    print(f"\nYou're feeling {mood}? Watch these:\n")
    for p in picks:
        print(f"  🎬  {p['title']} ({p['year']}) — {p['genre']}  ★{p['rating']}")
    print()
    return picks


def main():
    client   = get_client()
    resource = get_resource()

    create_table(resource)
    seed_movies(resource)

    print_table_info(client)

    list_all_movies(resource)

    get_movie(resource, "5")

    update_movie_rating(resource, "5", "9.0")

    delete_movie(resource, "15")

    recommend_by_mood(resource, "excited")
    recommend_by_mood(resource, "thoughtful")




if __name__ == "__main__":
    main()