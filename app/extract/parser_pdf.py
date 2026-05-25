from datetime import date, datetime
from pathlib import Path
import re
from collections.abc import Iterable
from typing import Optional

import pandas as pd
import pdfplumber

from logger import logger


def find_page_with_text(pdf: pdfplumber.PDF, target: str) -> int | None:
    """Возвращает индекс первой страницы, содержащей заданный текст."""
    for i, page in enumerate(pdf.pages):
        text = page.extract_text()
        if text and target in text:
            return i
    return None


def extract_headers_and_data(page: pdfplumber.page) -> tuple[list[str] | None, list[list[str]] | None]:
    """
    Извлекает строку заголовков и строки данных из таблицы на странице.
    Возвращает (headers, data_rows) или (None, None), если заголовок не найден.
    """
    tables = page.find_tables()
    if tables:
        for table in tables:
            extracted = table.extract()
            if not extracted or len(extracted) < 2:
                continue
            for idx, row in enumerate(extracted):
                row_text = " ".join(str(cell) for cell in row if cell)
                if re.search(r"Код\s+Инструмента", row_text, re.IGNORECASE):
                    headers = [str(cell).strip() if cell else "" for cell in row]
                    data_rows = extracted[idx + 1 :]
                    return headers, data_rows
    tables = page.extract_tables(
        table_settings={
            "vertical_strategy": "text",
            "horizontal_strategy": "text",
            "snap_tolerance": 3,
        }
    )
    if tables:
        for table in tables:
            if not table or len(table) < 2:
                continue
            for idx, row in enumerate(table):
                row_text = " ".join(str(cell) for cell in row if cell)
                if re.search(r"Код\s+Инструмента", row_text, re.IGNORECASE):
                    headers = [str(cell).strip() if cell else "" for cell in row]
                    data_rows = table[idx + 1 :]
                    return headers, data_rows
    return None, None


def collect_data_rows_from_pdf(pdf: pdfplumber.PDF, start_page: int, stop_patterns: Iterable[str]) -> list[list[str]]:
    """
    Собирает строки данных, начиная с указанной страницы, до тех пор,
    пока не встретится текст из stop_patterns на какой-либо странице.
    Возвращает список строк.
    """
    collected_rows = []
    for page_num in range(start_page, len(pdf.pages)):
        page = pdf.pages[page_num]
        text = page.extract_text()
        if text:
            for pattern in stop_patterns:
                if pattern in text:
                    return collected_rows

        tables = page.find_tables()
        if tables:
            for table in tables:
                extracted = table.extract()
                if extracted:
                    collected_rows.extend(extracted)
        else:
            tables = page.extract_tables(
                table_settings={
                    "vertical_strategy": "text",
                    "horizontal_strategy": "text",
                    "snap_tolerance": 3,
                }
            )
            for table in tables:
                if table:
                    collected_rows.extend(table)
    return collected_rows


def normalize_column_names(df: pd.DataFrame) -> dict:
    """Возвращает словарь: нормализованное имя -> исходное имя колонки."""
    normalized = {}
    for col in df.columns:
        norm = str(col).replace("\n", " ").replace("\r", " ").strip()
        norm = re.sub(r"\s+", " ", norm)
        normalized[norm] = col
    return normalized


def map_columns(df: pd.DataFrame, column_mapping: dict) -> pd.DataFrame:
    """
    Переименовывает колонки DataFrame согласно mapping.
    column_mapping: {target_name: [pattern1, pattern2, ...]}
    """
    norm_map = normalize_column_names(df)
    selected = {}
    for target, patterns in column_mapping.items():
        for norm_col, orig_col in norm_map.items():
            if any(re.search(p, norm_col.lower()) for p in patterns):
                selected[target] = orig_col
                break
    if len(selected) != len(column_mapping):
        missing = set(column_mapping.keys()) - set(selected.keys())
        raise ValueError(f"Не найдены колонки: {missing}")
    result = df[list(selected.values())].copy()
    result.columns = list(selected.keys())
    return result


def clean_numeric_columns(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """Очищает числовые колонки от пробелов, запятых и приводит к числовому типу."""
    df = df.replace("-", pd.NA)
    for col in cols:
        if col in df.columns:
            df[col] = df[col].astype(str).str.replace(r"[\s,]", "", regex=True)
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def make_headers_unique(headers: list[str]) -> list[str]:
    """Добавляет суффиксы к дублирующимся заголовкам."""
    seen = {}
    unique = []
    for h in headers:
        h_clean = str(h).strip() if h else ""
        if h_clean in seen:
            seen[h_clean] += 1
            unique.append(f"{h_clean}_{seen[h_clean]}")
        else:
            seen[h_clean] = 0
            unique.append(h_clean)
    return unique


def extract_spimex_bulletin_data(
    file_path: Path,
    trade_date: Optional[date] = None,
    product_prefixes: tuple[str, ...] = ("A",),
    stop_patterns: tuple[str, ...] = ("Единица измерения: Кубический метр", "Единица измерения: Килограмм"),
) -> list[dict]:
    logger.info(f"Обработка файла: {file_path}")
    with pdfplumber.open(file_path) as pdf:
        start_page = find_page_with_text(pdf, "Единица измерения: Метрическая тонна")
        if start_page is None:
            raise ValueError(f"Заголовок 'Единица измерения: Метрическая тонна' не найден в {file_path}")

        page = pdf.pages[start_page]
        headers, data_rows = extract_headers_and_data(page)
        if headers is None:
            raise ValueError(f"Не найдена строка заголовков на странице {start_page} в {file_path}")
        
        trade_date = _extract_trade_date_from_pdf(pdf)
        if trade_date is None:
            raise ValueError(f"Не удалось найти дату торгов в файле {file_path}")
        
        all_rows = data_rows if data_rows else []
        next_page_start = start_page + 1
        additional_rows = collect_data_rows_from_pdf(pdf, next_page_start, stop_patterns)
        all_rows.extend(additional_rows)

        # Делаем заголовки уникальными перед созданием DataFrame
        unique_headers = make_headers_unique(headers)
        df = pd.DataFrame(all_rows, columns=unique_headers)

        column_mapping = {
            "exchange_product_id": [r"код инструмента", r"код"],
            "exchange_product_name": [r"наименование инструмента", r"наименование"],
            "delivery_basis_name": [r"базис поставки", r"базис"],
            "volume": [r"объем договоров в единицах измерения", r"объем договоров", r"объем"],
            "total": [
                r"объем договоров,? руб\.?",
                r"обьем договоров,? руб\.?",
                r"объем,? руб\.?",
                r"обьем,? руб\.?",
                r"объем договоров руб",
                r"обьем договоров руб",
            ],
            "count": [r"количество договоров,? шт\.?", r"количество договоров", r"количество"],
        }
        result = map_columns(df, column_mapping)
        result = clean_numeric_columns(result, ["volume", "total", "count"])
        result = result.dropna(subset=["exchange_product_id", "volume", "total", "count"])

        result.insert(2, "oil_id", result["exchange_product_id"].astype(str).str[:4])
        result.insert(3, "delivery_basis_id", result["exchange_product_id"].astype(str).str[4:7])
        result.insert(5, "delivery_type_id", result["exchange_product_id"].astype(str).str[-1:])
        result["date"] = trade_date

        result = result[result["count"] > 0]

        if product_prefixes:
            pattern = "^(" + "|".join(re.escape(p) for p in product_prefixes) + ")"
            result = result[result["exchange_product_id"].str.match(pattern)]

        #         # В конце возвращаем записи из result (НЕ из исходного df)
        records = result.to_dict(orient="records")
        return records

def _extract_trade_date_from_pdf(pdf: pdfplumber.PDF) -> Optional[date]:
    """Ищет в первых трёх страницах строку 'Дата торгов: ДД.ММ.ГГГГ'."""
    for page_num in range(min(3, len(pdf.pages))):
        text = pdf.pages[page_num].extract_text()
        if not text:
            continue
        # Ищем "Дата торгов: 07.05.2026" или "Дата торгов:07.05.2026"
        match = re.search(r"Дата торгов:\s*(\d{2}\.\d{2}\.\d{4})", text)
        if match:
            date_str = match.group(1)
            try:
                return datetime.strptime(date_str, "%d.%m.%Y").date()
            except ValueError:
                continue
    return None
