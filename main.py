import asyncio
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from app.benchmark import async_timer
from app.database import Base, async_session_factory, engine
from app.download import AsyncFileDownloader
from app.extract.file_processor import process_file  # универсальный обработчик
from app.links import ParserLinkAsync
from app.orm import AsyncDBWriter
from config import DEST_FOLDER, MAX_DOWNLOAD_WORKERS, MAX_PROCESS_WORKERS, START_FROM, START_URL
from logger import logger
from app.models import SpimexTradingResult


async def link_producer(parser, url_queue, num_downloaders):
    """Собирает ссылки на файлы."""
    current_url = parser.base_url
    while current_url:
        html = await parser._fetch_page(current_url)
        new_links, stop = parser._extract_links_and_check_stop(html, current_url)
        for link in new_links:
            await url_queue.put(link)
        if stop:
            break
        next_url = parser._get_next_page_url(html, current_url)
        if not next_url:
            break
        current_url = next_url
        await asyncio.sleep(0.2)

    for _ in range(num_downloaders):
        await url_queue.put(None)


async def download_worker(url_queue, file_queue, downloader, dest_folder):
    """Скачивает файлы и кладёт (путь, расширение) в очередь."""
    while True:
        url = await url_queue.get()
        if url is None:
            url_queue.task_done()
            break

        result = await downloader._download_one(url, dest_folder)
        if result:
            filepath, ext = result
            await file_queue.put((filepath, ext))

        url_queue.task_done()


async def file_processor_worker(file_queue, record_queue, executor, trade_date):
    """Обрабатывает файлы в процессе."""
    loop = asyncio.get_running_loop()
    while True:
        item = await file_queue.get()
        if item is None:
            file_queue.task_done()
            break

        filepath, ext = item
        try:
            records = await loop.run_in_executor(executor, process_file, filepath, trade_date, ext)

            for rec in records:
                await record_queue.put(rec)

            logger.info(f"Извлечено {len(records)} записей из {filepath}")

        except Exception as e:
            logger.error(f"Ошибка обработки {filepath}: {e}")
        finally:
            Path(filepath).unlink(missing_ok=True)
            file_queue.task_done()


async def db_writer_worker(record_queue, db_writer):
    """Пишет записи в БД пачками."""
    batch = []
    total_processed = 0

    while True:
        rec = await record_queue.get()
        if rec is None:
            if batch:
                await db_writer.write_batch(batch)
                total_processed += len(batch)
            record_queue.task_done()
            logger.info(f"Запись в БД завершена. Всего обработано записей: {total_processed}")
            break

        batch.append(rec)
        if len(batch) >= db_writer.batch_size:
            await db_writer.write_batch(batch)
            total_processed += len(batch)
            batch.clear()

        record_queue.task_done()


async def main():
    async with async_timer("Полный цикл загрузки данных SPIMEX"):
        # Создаём таблицы
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Таблицы БД проверены/созданы")

        url_queue = asyncio.Queue()
        file_queue = asyncio.Queue()
        record_queue = asyncio.Queue()

        executor = ProcessPoolExecutor(max_workers=MAX_PROCESS_WORKERS)

        try:
            async with (
                ParserLinkAsync(START_FROM, START_URL) as parser,
                AsyncFileDownloader(max_concurrent=MAX_DOWNLOAD_WORKERS) as downloader,
            ):
                db_writer = AsyncDBWriter(session_factory=async_session_factory)

                # Запускаем сбор ссылок на файлы
                producer_task = asyncio.create_task(link_producer(parser, url_queue, MAX_DOWNLOAD_WORKERS))

                # Запускаем скачиваем файлы и кладём в очередь
                download_tasks = [
                    asyncio.create_task(download_worker(url_queue, file_queue, downloader, DEST_FOLDER))
                    for _ in range(MAX_DOWNLOAD_WORKERS)
                ]

                # Запускаем обрабатывает файлы
                processor_tasks = [
                    asyncio.create_task(file_processor_worker(file_queue, record_queue, executor, START_FROM))
                    for _ in range(MAX_PROCESS_WORKERS)
                ]

                # Запускаем записи в БД
                db_task = asyncio.create_task(db_writer_worker(record_queue, db_writer))

                # Ждём завершения producer
                await producer_task
                await url_queue.join()

                # Останавливаем downloaders
                for _ in download_tasks:
                    await url_queue.put(None)
                await asyncio.gather(*download_tasks)

                # Останавливаем processors
                for _ in processor_tasks:
                    await file_queue.put(None)
                await file_queue.join()
                await asyncio.gather(*processor_tasks)

                # Останавливаем DB writer
                await record_queue.put(None)
                await record_queue.join()
                await db_task

        finally:
            # Закрываем executor только после завершения всех задач
            executor.shutdown(wait=True)
            logger.info("ProcessPoolExecutor завершён")


if __name__ == "__main__":
    asyncio.run(main())
