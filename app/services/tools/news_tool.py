"""News tool: bridge to repository's NewsDataService.
Uses app.services.news_data_service.get_news_data_service for real data.
"""

from typing import List, Dict, Any, Optional

from app.services.news_data_service import get_news_data_service


class NewsTool:
    async def fetch_news(self, symbol: Optional[str] = None, limit: int = 10, hours_back: int = 24) -> List[Dict[str, Any]]:
        """Fetch recent news using the project's NewsDataService.

        Args:
            symbol: stock symbol (optional). If omitted, returns global latest news.
            limit: number of items to return.
            hours_back: lookback in hours for latest news (used when symbol omitted or to restrict recency).
        Returns:
            List of news documents as returned by NewsDataService.get_latest_news / query_news.
        """
        service = await get_news_data_service()
        # Prefer symbol-specific latest news
        if symbol:
            return await service.get_latest_news(symbol=symbol, limit=limit, hours_back=hours_back)
        # fallback: query recent across all symbols
        return await service.get_latest_news(symbol=None, limit=limit, hours_back=hours_back)
