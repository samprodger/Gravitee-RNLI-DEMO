import asyncio
import json
import logging
import os
from datetime import datetime, timezone

import httpx
from aiokafka import AIOKafkaProducer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

RNLI_API_URL = "https://services.rnli.org/api/launches"
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "redpanda:9092")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "rnli.launches")
POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "30"))


async def fetch_launches(client: httpx.AsyncClient) -> list[dict]:
    try:
        response = await client.get(RNLI_API_URL, timeout=15.0)
        response.raise_for_status()
        return response.json()
    except httpx.HTTPStatusError as exc:
        logger.error("HTTP error fetching launches: %s %s", exc.response.status_code, exc.response.text)
        return []
    except httpx.RequestError as exc:
        logger.error("Request error fetching launches: %s", exc)
        return []
    except Exception as exc:
        logger.error("Unexpected error fetching launches: %s", exc)
        return []


async def main():
    logger.info("Starting RNLI launches poller")
    logger.info("Kafka bootstrap: %s | topic: %s | poll interval: %ss",
                KAFKA_BOOTSTRAP_SERVERS, KAFKA_TOPIC, POLL_INTERVAL_SECONDS)

    producer = AIOKafkaProducer(
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
    )
    await producer.start()
    logger.info("Kafka producer connected")

    seen_ids: set[int] = set()

    headers = {"User-Agent": "Mozilla/5.0 (compatible; RNLI-Launches-Poller/1.0)"}
    async with httpx.AsyncClient(headers=headers) as client:
        # Seed seen_ids from the current feed on startup — avoids replaying
        # everything that already existed before this process started.
        logger.info("Seeding seen launch IDs from current feed…")
        seed_launches = await fetch_launches(client)
        for launch in seed_launches:
            seen_ids.add(launch["id"])
        logger.info("Seeded %d existing launch IDs — only future launches will be published", len(seen_ids))

        try:
            while True:
                launches = await fetch_launches(client)

                new_launches = [l for l in launches if l["id"] not in seen_ids]

                if not new_launches:
                    logger.info("No new launches (total in feed: %d)", len(launches))
                else:
                    polled_at = datetime.now(timezone.utc).isoformat()
                    for launch in new_launches:
                        event = {**launch, "polledAt": polled_at}
                        await producer.send_and_wait(
                            KAFKA_TOPIC,
                            value=event,
                            key=str(launch["id"]).encode("utf-8"),
                        )
                        seen_ids.add(launch["id"])
                        logger.info(
                            "Published launch — station: %s | date: %s | id: %s",
                            launch.get("shortName", "unknown"),
                            launch.get("launchDate", "unknown"),
                            launch["id"],
                        )

                await asyncio.sleep(POLL_INTERVAL_SECONDS)

        except asyncio.CancelledError:
            logger.info("Poller cancelled — shutting down")
        finally:
            await producer.stop()
            logger.info("Kafka producer stopped")


if __name__ == "__main__":
    asyncio.run(main())
