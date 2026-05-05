import re
from datetime import date

import pandas as pd
import xlrd

from logger import logger


def extract_spimex_xls_data(
    file_path: str,
    trade_date: date,
    product_prefixes: tuple[str, ...] = ("A",),
) -> list[dict]:
    """
    Извлекает данные из XLS-файла бюллетеня SPIMEX.
    Возвращает список словарей для записи в БД.
    """
    logger.info(f"Обработка XLS файла: {file_path}")

    try:
        # Открываем XLS файл
        workbook = xlrd.open_workbook(file_path)
        sheet = workbook.sheet_by_index(0)

        if sheet.nrows < 2:
            logger.warning(f"Файл {file_path} содержит менее 2 строк")
            return []

        # Ищем строку с заголовками (содержит "Код Инструмента")
        header_row_idx = None
        for row_idx in range(sheet.nrows):
            row_text = " ".join(str(sheet.cell_value(row_idx, col)) for col in range(sheet.ncols))
            if re.search(r"Код\s+Инструмента", row_text, re.IGNORECASE):
                header_row_idx = row_idx
                break

        if header_row_idx is None:
            logger.warning(f"Заголовок 'Код Инструмента' не найден в {file_path}")
            return []

        # Извлекаем заголовки
        headers = [str(sheet.cell_value(header_row_idx, col)).strip() for col in range(sheet.ncols)]

        # Собираем строки данных до стоп-паттернов
        data_rows = []
        stop_patterns = ["Единица измерения: Кубический метр", "Единица измерения: Килограмм", "Итого:"]

        for row_idx in range(header_row_idx + 1, sheet.nrows):
            row_values = [sheet.cell_value(row_idx, col) for col in range(sheet.ncols)]
            row_text = " ".join(str(v) for v in row_values)

            # Проверяем стоп-паттерны
            if any(pattern in row_text for pattern in stop_patterns):
                break

            # Пропускаем пустые строки
            if all(v == "" or v is None for v in row_values):
                continue

            data_rows.append(row_values)

        if not data_rows:
            logger.warning(f"Нет данных в {file_path}")
            return []

        # Создаём DataFrame
        df = pd.DataFrame(data_rows, columns=headers[: len(data_rows[0])])

        # Маппинг колонок (аналогично PDF)
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
                r"оборот",
            ],
            "count": [r"количество договоров,? шт\.?", r"количество договоров", r"количество"],
        }

        # Нормализуем и маппим колонки
        result = map_columns_dataframe(df, column_mapping)

        # Очищаем числовые данные
        result = clean_numeric_columns(result, ["volume", "total", "count"])
        result = result.dropna(subset=["exchange_product_id", "volume", "total", "count"])

        # Добавляем производные колонки
        result.insert(2, "oil_id", result["exchange_product_id"].astype(str).str[:4])
        result.insert(3, "delivery_basis_id", result["exchange_product_id"].astype(str).str[4:7])
        result.insert(5, "delivery_type_id", result["exchange_product_id"].astype(str).str[-1:])
        result["date"] = trade_date

        # Фильтруем
        result = result[result["count"] > 0]

        if product_prefixes:
            pattern = "^(" + "|".join(re.escape(p) for p in product_prefixes) + ")"
            result = result[result["exchange_product_id"].str.match(pattern)]

        result = result.reset_index(drop=True)
        records = result.to_dict(orient="records")

        logger.info(f"Успешно извлечено {len(records)} записей из XLS {file_path}")
        return records

    except Exception as e:
        logger.error(f"Ошибка обработки XLS {file_path}: {e}")
        return []


def map_columns_dataframe(df: pd.DataFrame, column_mapping: dict) -> pd.DataFrame:
    """Вспомогательная функция маппинга колонок. Нормализуем имена колонок."""
    norm_map = {}
    for col in df.columns:
        norm = str(col).replace("\n", " ").replace("\r", " ").strip()
        norm = re.sub(r"\s+", " ", norm)
        norm_map[norm] = col

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
