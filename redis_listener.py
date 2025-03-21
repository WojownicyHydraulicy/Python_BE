import logging
import redis
import time
from api import assign_order_to_worker 

# Logger config
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Redis connection
redis_client = redis.Redis(host="localhost", port=6379, db=0, decode_responses=True)

def listen_for_events():
    # Listening for new events
    pubsub = redis_client.pubsub()
    pubsub.subscribe("new_order_arrived", "worker_available")

    logger.info("Redis Listener started.")

    while True:
        try:
            message = pubsub.get_message(ignore_subscribe_messages=True, timeout=5)
            if message:
                event_type = message["channel"]
                if event_type == "new_order_arrived":
                    logger.info(f"New order: {message['data']}")
                    assign_order_to_worker()
                elif event_type == "worker_available":
                    logger.info(f"Worker available: {message['data']}")
                    assign_order_to_worker()

            time.sleep(1)  
        except Exception as e:
            logger.error(f"Błąd w Redis Listener: {e}")
            time.sleep(5)  

if __name__ == "__main__":
    listen_for_events()
