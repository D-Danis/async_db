from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models import SpimexTradingResult
from logger import logger


class AsyncDBWriter:
    """Принимает записи из record_queue и пишет пачками в БД."""

    def __init__(self, session_factory, batch_size: int = 1000):
        self.sessionmaker = session_factory
        self.batch_size = batch_size
        self.total_inserted = 0

    async def write_batch(self, records: list[dict]):
        if not records:
            return

        try:
            async with self.sessionmaker() as session:
                stmt = pg_insert(SpimexTradingResult).values(records)
                stmt = stmt.on_conflict_do_nothing(index_elements=["exchange_product_id", "date"])
                result = await session.execute(stmt)
                await session.commit()

                inserted = result.rowcount
                duplicates = len(records) - inserted
                self.total_inserted += inserted

                logger.info(
                    f"Запись в БД: добавлено {inserted} записей, "
                    f"пропущено дубликатов {duplicates}, "
                    f"всего с начала сессии {self.total_inserted}"
                )
        except Exception as e:
            logger.error(f"Ошибка записи в БД: {e}")
            raise
