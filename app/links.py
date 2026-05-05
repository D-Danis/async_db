import asyncio
from datetime import date
from urllib.parse import urljoin

import aiohttp
from bs4 import BeautifulSoup
from dateutil import parser

from logger import logger


class ParserLinkAsync:
    def __init__(self, start_from: date, base_url: str):
        self.start_date = start_from
        self.base_url = base_url
        self.links = []
        self._next_css_sel = "li.bx-pag-next a"
        self._link_css_sel = "#comp_d609bce6ada86eff0b6f7e49e6bae904 div.accordeon-inner__wrap-item a"
        self.session = None

    async def __aenter__(self):
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    @staticmethod
    def get_file_date(file_url: str) -> date:
        date = parser.parse(file_url.split("_")[-1][:8]).date()
        return date

    async def _fetch_page(self, url: str) -> str:
        retries = 3
        for attempt in range(retries):
            try:
                async with self.session.get(url) as resp:
                    resp.raise_for_status()
                    return await resp.text()
            except Exception as e:
                logger.warning(f"Попытка {attempt + 1}/{retries} для {url}: {e}")
                if attempt == retries - 1:
                    raise
                await asyncio.sleep(1)

    def _extract_links_and_check_stop(self, html: str, current_url: str):
        """
        Возвращает кортеж (links, stop_flag).
        stop_flag = True, если на странице есть хотя бы один файл с датой < start_date.
        """
        soup = BeautifulSoup(html, "html.parser")
        anchors = soup.select(self._link_css_sel)
        file_links = []
        stop = False

        for a in anchors:
            href = a.get("href")
            if not href:
                continue
            try:
                file_date = self.get_file_date(href)
            except ValueError:
                continue

            if file_date < self.start_date:
                stop = True
                break

            # Преобразуем относительную ссылку в абсолютную
            full_url = urljoin(current_url, href)
            file_links.append(full_url)

        return file_links, stop

    def _get_next_page_url(self, html: str, current_url: str) -> str | None:
        soup = BeautifulSoup(html, "html.parser")
        next_el = soup.select_one(self._next_css_sel)
        if next_el and next_el.get("href"):
            return urljoin(current_url, next_el["href"])
        return None

    async def grab_links(self) -> list[str]:
        """Обходит страницы, останавливается при появлении файла с датой < start_date."""
        logger.debug("Начинаем асинхронный сбор ссылок")
        current_url = self.base_url

        while current_url:
            html = await self._fetch_page(current_url)
            new_links, stop = self._extract_links_and_check_stop(html)

            if new_links:
                self.links.extend(new_links)
                logger.debug(f"Собрано {len(new_links)} ссылок со страницы {current_url}")

            if stop:
                logger.info(f"На странице {current_url} найден файл с датой раньше {self.start_date}. Остановка.")
                break

            next_url = self._get_next_page_url(html, current_url)
            if not next_url:
                break
            current_url = next_url
            logger.info(f"Переход на следующую страницу: {current_url}")
            await asyncio.sleep(0.2)

        logger.info(f"Итоговые ссылки: {self.links}")
        return self.links
