"""MongoDB connection and share-link metadata helpers."""
import logging
import secrets
import string

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import DuplicateKeyError

import config

log = logging.getLogger(__name__)

mongo_client = None
db = None
links_collection = None

_LINK_ALPHABET = string.ascii_letters + string.digits


async def connect_database():
    global mongo_client, db, links_collection
    if not config.MONGO_URI:
        raise RuntimeError("MONGO_URI is missing.")
    mongo_client = AsyncIOMotorClient(config.MONGO_URI, serverSelectionTimeoutMS=10000)
    try:
        await mongo_client.admin.command("ping")
        db = mongo_client[config.DB_NAME]
        links_collection = db["share_links"]
        await links_collection.create_index("link_id", unique=True)
        await links_collection.create_index("created_at")
    except Exception:
        mongo_client.close()
        mongo_client = None
        links_collection = None
        raise
    return links_collection


async def close_database():
    global mongo_client
    if mongo_client:
        mongo_client.close()
        mongo_client = None


def get_links_collection():
    if links_collection is None:
        raise RuntimeError("MongoDB is not connected.")
    return links_collection


def new_link_id(length=12):
    return "".join(secrets.choice(_LINK_ALPHABET) for _ in range(length))


async def insert_link(record):
    """Insert a share-link record with a fresh unique link_id and return that id."""
    collection = get_links_collection()
    for _ in range(5):
        record["link_id"] = new_link_id()
        try:
            await collection.insert_one(record)
            return record["link_id"]
        except DuplicateKeyError:
            continue
    raise RuntimeError("Could not generate a unique link ID.")
