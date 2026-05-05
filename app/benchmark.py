import time
from contextlib import asynccontextmanager

from logger import logger


class Timer:
    def __init__(self, name: str = "Операция"):
        self.name = name
        self.start_time = None

    def start(self):
        self.start_time = time.perf_counter()
        logger.info(f"[Таймер] {self.name} начата")

    def stop(self):
        if self.start_time:
            elapsed = time.perf_counter() - self.start_time
            logger.info(f"[Таймер] {self.name} завершена за {elapsed:.2f} сек")
            return elapsed
        return 0


@asynccontextmanager
async def async_timer(name: str = "Операция"):
    """Асинхронный контекстный менеджер для замера времени."""
    timer = Timer(name)
    timer.start()
    try:
        yield timer
    finally:
        timer.stop()
