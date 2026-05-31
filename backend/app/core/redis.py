"""Redis connection configuration for RQ job queue."""
from redis import Redis
from rq import Queue

from app.core.config import settings


def get_redis_connection() -> Redis:
    """
    Get Redis connection instance.
    
    Returns:
        Redis connection configured from settings
    """
    return Redis.from_url(settings.redis_url)


def get_queue(name: str = "default") -> Queue:
    """
    Get RQ queue instance.
    
    Args:
        name: Queue name (default: "default")
        
    Returns:
        RQ Queue instance
    """
    return Queue(name, connection=get_redis_connection())


# Default queue for pipeline jobs
pipeline_queue = Queue("pipeline", connection=get_redis_connection())
