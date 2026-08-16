import os

DATABASE_URL = os.environ.get(
    "TOWER_DATABASE_URL", "postgresql+psycopg://tower:tower@localhost:5433/tower"
)
REDIS_URL = os.environ.get("TOWER_REDIS_URL", "redis://localhost:6380/0")
EVENTS_CHANNEL = "tower.events"

# A session whose last heartbeat is older than this is marked stale.
HEARTBEAT_TIMEOUT_S = int(os.environ.get("TOWER_HEARTBEAT_TIMEOUT_S", "45"))
STALE_SWEEP_INTERVAL_S = 15
