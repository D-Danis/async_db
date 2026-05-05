from datetime import date
from pathlib import Path
from typing import Any

from app.extract.parser_pdf import extract_spimex_bulletin_data as parse_pdf
from app.extract.parser_xls import extract_spimex_xls_data as parse_xls
from logger import logger


def process_file(file_path: Path, trade_date: date, ext: str) -> list[dict[str, Any]]:
    """
    Маршрутизатор: выбирает парсер в зависимости от расширения файла.
    Возвращает список записей для БД.
    """

    if ext == ".pdf":
        return parse_pdf(file_path, trade_date)
    elif ext in (".xls", ".xlsx"):
        return parse_xls(file_path, trade_date)
    else:
        logger.warning(f"Неподдерживаемый формат файла: {file_path}")
        return []
