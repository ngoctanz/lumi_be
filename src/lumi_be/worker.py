import logging
import signal
import threading

from pymongo.errors import PyMongoError

from .config import Settings
from .db import database
from .providers.widgets import refresh


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    stopping = threading.Event()

    def stop(*_):
        stopping.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    client, db = database(Settings())
    try:
        while True:
            try:
                result = refresh(db)
                logging.getLogger("lumi.worker").info(
                    "providers updated=%d failed=%d", result["updated"], result["failed"]
                )
            except PyMongoError:
                logging.getLogger("lumi.worker").error("Database unavailable; retrying on next cycle")
            stopping.wait(300)
    finally:
        client.close()


if __name__ == "__main__":
    main()
