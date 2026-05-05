import asyncio

from pathlib import Path
from typing import Optional

import aiohttp
import aiofiles

from logger import logger


class AsyncFileDownloader:
    def __init__(self, max_concurrent: int = 3, retries: int = 5, delay: float = 0.5):
        """
        :param max_concurrent: Максимум одновременных загрузок 
        :param retries: попыток при ошибках
        :param delay: Задержка между запросами в секундах
        """
        self.max_concurrent = max_concurrent
        self.retries = retries
        self.delay = delay
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self.session = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=60),
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            },
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    @staticmethod
    def get_filename_and_ext(url: str) -> tuple[str, str]:
        """Извлекает имя файла и расширение из URL."""
        filename = url.split("/")[-1].split("?")[0] or "index"
        ext = Path(filename).suffix.lower()
        if not ext:
            ext = ".pdf"  
            filename += ext
        return filename, ext
    
    async def _download_one(self, url: str, dest_folder: str) -> Optional[tuple[str, str]]:
        filename, ext = self.get_filename_and_ext(url)
        filepath = Path(dest_folder) / filename

        async with self.semaphore:
            for attempt in range(1, self.retries + 1):
                try:
                    await asyncio.sleep(self.delay)
                    
                    async with self.session.get(url) as response:
                        if response.status == 503:
                            wait_time = 2 ** attempt
                            logger.warning(f"Сервер недоступен (503), ожидание {wait_time}с...")
                            await asyncio.sleep(wait_time)
                            continue
                            
                        response.raise_for_status()
                        async with aiofiles.open(filepath, "wb") as f:
                            async for chunk in response.content.iter_chunked(1024 * 16):
                                await f.write(chunk)
                        
                        logger.info(f"Скачан: {url} -> {filepath} [тип: {ext}]")
                        return str(filepath), ext
                        
                except aiohttp.ClientResponseError as e:
                    if e.status == 503:
                        wait_time = 2 ** attempt
                        logger.warning(f"Попытка {attempt}/{self.retries} для {url}: 503 Service Unavailable, ожидание {wait_time}с")
                        await asyncio.sleep(wait_time)
                    else:
                        logger.warning(f"Попытка {attempt}/{self.retries} для {url}: {e}")
                        if attempt == self.retries:
                            logger.error(f"Не удалось скачать {url}")
                            return None
                        await asyncio.sleep(2 ** attempt)
                        
                except Exception as e:
                    logger.warning(f"Попытка {attempt}/{self.retries} для {url}: {e}")
                    if attempt == self.retries:
                        logger.error(f"Не удалось скачать {url}")
                        return None
                    await asyncio.sleep(2 ** attempt)
        return None

    async def download_all(self, urls: list[str], dest_folder: str) -> tuple[int, int]:
        """
        Асинхронно загружает все файлы из urls в dest_folder.
        Возвращает (успешно, с ошибками).
        """
        if not self.session:
            raise RuntimeError("AsyncFileDownloader должен использоваться как контекстный менеджер (async with)")

        Path(dest_folder).mkdir(parents=True, exist_ok=True)

        tasks = [self._download_one(url, dest_folder) for url in urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        success = sum(1 for r in results if r is True)
        failed = sum(1 for r in results if r is not True)
        logger.info(f"Загрузка завершена: успешно {success}, ошибок {failed}")
        return success, failed
