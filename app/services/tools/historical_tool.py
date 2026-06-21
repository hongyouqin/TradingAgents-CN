"""Historical data tool adapter - uses HistoricalDataService for real data."""

from typing import List, Dict, Any, Optional

from app.services.historical_data_service import get_historical_data_service


class HistoricalTool:
    async def fetch_historical(self, symbol: str, start_date: Optional[str] = None, end_date: Optional[str] = None, period: str = "daily", limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Fetch historical OHLC data from project's HistoricalDataService.

        Args:
            symbol: stock symbol
            start_date, end_date: optional YYYY-MM-DD
            period: daily/weekly/monthly
            limit: max number of rows
        """
        service = await get_historical_data_service()
        return await service.get_historical_data(symbol=symbol, start_date=start_date, end_date=end_date, period=period, limit=limit)
