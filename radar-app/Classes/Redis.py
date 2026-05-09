import os
import redis
from typing import Optional


class RedisClient:
    _instance: Optional["RedisClient"] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init_connection()
        return cls._instance
    
    def _init_connection(self):
        host = os.getenv("REDIS_HOST", "localhost")
        port = int(os.getenv("REDIS_PORT", 6379))
        
        # Main client with timeouts
        self.client = redis.Redis(
            host=host,
            port=port,
            decode_responses=True,
            socket_keepalive=True,
            health_check_interval=30
        )
        
        # Store connection params for pubsub
        self._host = host
        self._port = port
    
    def get_pubsub_client(self):
        """Separate client for pubsub - no timeout"""
        return redis.Redis(
            host=self._host,
            port=self._port,
            decode_responses=True,
            socket_keepalive=True,
            socket_timeout=None  # No timeout for pubsub
        )
    
    def ping(self) -> bool:
        try:
            return self.client.ping()
        except redis.ConnectionError:
            return False
    
    def get(self, key: str) -> Optional[str]:
        return self.client.get(key)
    
    def set(self, key: str, value: str, ex: int = None) -> bool:
        return self.client.set(key, value, ex=ex)
    
    def hgetall(self, key: str) -> dict:
        return self.client.hgetall(key)
    
    def hset(self, key: str, mapping: dict) -> int:
        return self.client.hset(key, mapping=mapping)
    
    def exists(self, key: str) -> bool:
        return self.client.exists(key) > 0
    
    def scan_iter(self, match: str = None, count: int = 100):
        return self.client.scan_iter(match=match, count=count)
    
    def lrange(self, key: str, start: int, end: int) -> list:
        return self.client.lrange(key, start, end)
    
    def expire(self, key: str, seconds: int) -> bool:
        return self.client.expire(key, seconds)
    
    def delete(self, *keys) -> int:
        return self.client.delete(*keys)


def getRedis() -> RedisClient:
    return RedisClient()
