import json
from fastapi import Request
from fastapi.responses import StreamingResponse
from Classes.ApiBase import ApiBase
from Classes.Redis import getRedis


class ApiStream(ApiBase):
    
    title = "Stream"
    description = "Server-Sent Events stream for live plane updates"
    
    apiParameters = {}
    
    def execute(self):
        # This won't be called - we handle streaming differently
        pass
    
    @staticmethod
    async def stream_generator():
        redis = getRedis()
        pubsub = redis.client.pubsub(ignore_subscribe_messages=True)
        pubsub.subscribe("planes_out")
        
        try:
            for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                data = message.get("data")
                if not data:
                    continue
                yield f"data: {data}\n\n"
        except GeneratorExit:
            pass
        finally:
            try:
                pubsub.close()
            except Exception:
                pass
    
    def requiresGet(self):
        return True
