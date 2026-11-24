from __future__ import annotations

from io import BytesIO

import openpyxl

from pipeline.providers.world_bank_commodities import parse_monthly_workbook


def workbook_bytes() -> bytes:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "Monthly Prices"
    sheet.append(["World Bank Commodity Price Data (The Pink Sheet)"])
    sheet.append(["monthly prices in nominal US dollars, 1960 to present"])
    sheet.append([None])
    sheet.append([None, "Crude oil, Brent", "Aluminum"])
    sheet.append([None, "($/bbl)", "($/mt)"])
    sheet.append(["2026M09", 67.4, 2700.0])
    sheet.append(["2026M10", 70.2, 2800.0])
    result = BytesIO()
    workbook.save(result)
    return result.getvalue()


def test_parses_new_approved_monthly_commodity_series_with_units() -> None:
    prices = parse_monthly_workbook(workbook_bytes(), ["WB:BRENT", "WB:ALUMINUM"])

    assert prices["WB:BRENT"][-1]["value"] == 70.2
    assert prices["WB:BRENT"][-1]["unit"] == "$/bbl"
    assert prices["WB:ALUMINUM"][-1]["value"] == 2800.0
    assert prices["WB:ALUMINUM"][-1]["unit"] == "$/mt"
