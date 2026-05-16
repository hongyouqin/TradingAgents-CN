import logging
from typing import Dict, Any, List
from datetime import datetime

logger = logging.getLogger(__name__)


class DataAggregatorAgent:
    """Assemble a compact context for downstream agents from cleaned daily_market_data."""

    async def run(self, db, daily_data: Dict[str, Any]) -> Dict[str, Any]:
        # daily_data is expected in the format saved by ForecastDataPipeline
        ctx: Dict[str, Any] = {}
        ctx['date'] = daily_data.get('date')
        ctx['market_basic'] = daily_data.get('market_basic', {})
        ctx['emotion_data'] = daily_data.get('emotion_data', {})
        ctx['sector_data'] = daily_data.get('sector_data', {})
        ctx['fund_flow'] = daily_data.get('fund_flow', {})
        ctx['macro_news'] = daily_data.get('macro_news', {})
        ctx['tech_index'] = daily_data.get('tech_index', {})
        ctx['stock_popular'] = daily_data.get('stock_popular', {})
        ctx['history_benchmark'] = daily_data.get('history_benchmark', {})

        # derived fields
        try:
            mb = ctx['market_basic']
            if isinstance(mb, dict):
                ctx['market_avg_pct'] = float(mb.get('avg_pct', 0) or 0)
                ctx['market_sum_amount'] = float(mb.get('sum_amount', 0) or 0)
        except Exception:
            ctx['market_avg_pct'] = 0.0
            ctx['market_sum_amount'] = 0.0

        return ctx


class EmotionAgent:
    """Infer a short emotion cycle and score (0-10) from aggregated context.

    This implementation uses deterministic heuristics as a safe default. Integrate
    an LLM or more advanced model by replacing the body and returning the same
    output shape.
    """

    async def run(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        emot = ctx.get('emotion_data', {})
        total = emot.get('total') or emot.get('total_count') or 0
        limit_up = emot.get('limit_up', 0)
        limit_down = emot.get('limit_down', 0)
        strong_up = emot.get('strong_up', 0)
        strong_down = emot.get('strong_down', 0)

        # heuristic score: more limit_up and strong_up -> higher score
        score = 5.0
        try:
            if total:
                up_ratio = (limit_up + strong_up) / max(1, total)
                down_ratio = (limit_down + strong_down) / max(1, total)
                score = 5.0 + (up_ratio - down_ratio) * 10.0
        except Exception:
            score = 5.0

        # clamp
        score = max(0.0, min(10.0, score))

        # qualitative label
        label = '中性'
        if score >= 7.0:
            label = '偏多'
        elif score <= 3.0:
            label = '偏空'

        return {"emotion_score": round(score, 2), "emotion_label": label, "meta": {"limit_up": int(limit_up), "limit_down": int(limit_down)}}


class SectorAgent:
    """Analyze sector rotation and prioritize sectors for next day.

    Uses industry data from stock_basic_info join when available.
    Falls back to analyzing top individual movers when sector aggregation fails.
    """

    async def run(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        sector_data = ctx.get('sector_data', {})
        top_sectors: List[Dict[str, Any]] = sector_data.get('top_sectors') or []
        top_stocks_fallback = sector_data.get('top_stocks_fallback') or {}
        sector_note = sector_data.get('note', '')

        prioritized: List[Dict[str, Any]] = []

        if top_sectors:
            # Primary path: use industry-aggregated sectors
            for s in top_sectors[:20]:
                score = 0.0
                try:
                    score += float(s.get('sum_amount', 0)) / (1e8 + 1)  # normalize by large number
                    score += float(s.get('avg_pct', 0)) * 2.0
                except Exception:
                    pass
                prioritized.append({
                    "industry": s.get('industry'),
                    "score": round(score, 4),
                    "count": int(s.get('count', 0)),
                    "avg_pct": s.get('avg_pct')
                })

            # sort descending
            prioritized.sort(key=lambda x: x['score'], reverse=True)
            note = "heuristic_priority"
        else:
            # Fallback: derive sector-like insights from top individual movers
            gainers = top_stocks_fallback.get('gainers', [])
            losers = top_stocks_fallback.get('losers', [])

            # Build pseudo-sectors from top movers as "热点个股" (hot stocks)
            hot_stocks = []
            for g in gainers[:10]:
                code = g.get('code', '')
                name = g.get('name', '')
                pct = g.get('pct_chg', 0)
                if code:
                    hot_stocks.append({
                        "code": str(code),
                        "name": str(name) if name else str(code),
                        "pct_chg": round(float(pct), 2),
                        "type": "强势股"
                    })

            cold_stocks = []
            for l in losers[:5]:
                code = l.get('code', '')
                name = l.get('name', '')
                pct = l.get('pct_chg', 0)
                if code:
                    cold_stocks.append({
                        "code": str(code),
                        "name": str(name) if name else str(code),
                        "pct_chg": round(float(pct), 2),
                        "type": "弱势股"
                    })

            # Synthesize a note explaining why we're showing individual stocks
            note = f"fallback_individual_stocks|reason={sector_note}"

            # Return as candidate entries with a synthesized format
            prioritized = [
                {"individual_hot_stocks": hot_stocks, "individual_cold_stocks": cold_stocks, "note": note}
            ]

        return {"sector_candidates": prioritized[:6], "note": note}


class OutputAgent:
    """Integrate all agent outputs into a final forecast and human readable text.

    The output includes machine-readable fields and a short narrative. Uses LLM
    when available, with a structured heuristic fallback that never produces
    empty placeholders.
    """

    async def run(self, ctx: Dict[str, Any], emotion: Dict[str, Any], sector: Dict[str, Any], user: dict = None) -> Dict[str, Any]:
        date = ctx.get('date') or datetime.utcnow().date().isoformat()
        trend_score = 50.0 + (emotion.get('emotion_score', 5.0) - 5.0) * 5.0
        trend_score = max(0.0, min(100.0, trend_score))

        sector_list = sector.get('sector_candidates', [])

        # Extract sector names from industry-based candidates
        top_sector_names = []
        for s in sector_list:
            industry = s.get('industry')
            if industry:
                top_sector_names.append(str(industry))

        # Check for fallback individual stock data
        individual_data = None
        if not top_sector_names:
            for s in sector_list:
                hot = s.get('individual_hot_stocks')
                cold = s.get('individual_cold_stocks')
                if hot or cold:
                    individual_data = {"hot": hot, "cold": cold}
                    break

        # Build data source summary for traceability
        data_sources = self._build_data_sources(ctx, emotion, sector)

        # Build LLM prompt with richer context
        llm_used = False
        try:
            from app.services.forecast_llm_service import call_llm_with_billing
            import json

            # Build a richer prompt that includes all available data
            prompt_obj = {
                "date": date,
                "market_basic": {
                    "avg_pct": ctx.get('market_avg_pct', 0),
                    "sum_amount": ctx.get('market_sum_amount', 0),
                    "total_count": ctx.get('market_basic', {}).get('total_count', 0),
                    "count_up": ctx.get('market_basic', {}).get('count_up', 0),
                    "count_down": ctx.get('market_basic', {}).get('count_down', 0),
                    "limit_up": ctx.get('market_basic', {}).get('limit_up', 0),
                    "limit_down": ctx.get('market_basic', {}).get('limit_down', 0),
                },
                "emotion": {
                    "score": emotion.get('emotion_score'),
                    "label": emotion.get('emotion_label'),
                    "limit_up": emotion.get('meta', {}).get('limit_up', 0),
                    "limit_down": emotion.get('meta', {}).get('limit_down', 0),
                },
                "sectors": top_sector_names if top_sector_names else None,
                "individual_movers": {
                    "hot": [{"name": s.get("name"), "code": s.get("code"), "pct_chg": s.get("pct_chg")} for s in (individual_data.get("hot", [])[:8] if individual_data else [])],
                    "cold": [{"name": s.get("name"), "code": s.get("code"), "pct_chg": s.get("pct_chg")} for s in (individual_data.get("cold", [])[:5] if individual_data else [])]
                } if individual_data else None,
                "fund_flow": ctx.get('fund_flow', {}).get('data') if isinstance(ctx.get('fund_flow'), dict) else None,
                "macro_news_count": len(ctx.get('macro_news', {}).get('items', [])) if isinstance(ctx.get('macro_news'), dict) else 0,
            }
            prompt = (
                "请基于以下结构化市场数据，生成明日市场前瞻（中文）:\n" + json.dumps(prompt_obj, ensure_ascii=False)
                + "\n\n输出要求：\n"
                + "1) 给出趋势评分（0-100）；\n"
                + "2) 一段不超过150字的开盘预判与重点关注（narrative），如果板块数据为空，请从强势个股中推理可能的热点方向；\n"
                + "3) 简要策略建议（short）；\n"
                + "4) 风险提示列表（risk_tips）。\n"
                + "请以JSON格式返回，键名: trend_score(数字), narrative(字符串), short(字符串), risk_tips(字符串数组)。"
            )

            success, llm_response, billing = await call_llm_with_billing(user, prompt, model_name=None, max_output_tokens=500)

            if success and llm_response:
                narrative = llm_response.strip()
                llm_used = True
            else:
                narrative = self._build_fallback_narrative(date, emotion, trend_score, top_sector_names, individual_data)
        except Exception:
            narrative = self._build_fallback_narrative(date, emotion, trend_score, top_sector_names, individual_data)

        # Strategy suggestion — contextual
        emo_score = emotion.get('emotion_score', 5)
        if emo_score >= 7:
            strategy_short = "优选高质量趋势股，注意高位回撤风险"
        elif emo_score <= 3:
            strategy_short = "防御为主，控制仓位，关注超跌反弹机会"
        else:
            strategy_short = "控制仓位，关注强势板块轮动"

        # Risk tips — derive from data
        risk_tips = self._derive_risk_tips(ctx, emotion, sector)

        forecast = {
            "date": date,
            "market_forecast": {
                "trend_score": round(trend_score, 2),
                "narrative": narrative
            },
            "emotion_forecast": emotion,
            "sector_forecast": sector_list,
            "strategy_suggest": {
                "short": strategy_short
            },
            "risk_tips": risk_tips,
            "data_sources": data_sources,
            "agent_log": {
                "generated_at": datetime.utcnow().isoformat(),
                "components": {"aggregator": True, "emotion": True, "sector": True, "output": True},
                "llm_used": llm_used,
                "sector_data_available": len(top_sector_names) > 0,
                "individual_fallback_used": individual_data is not None
            }
        }

        return forecast

    def _build_fallback_narrative(self, date: str, emotion: Dict[str, Any], trend_score: float,
                                  top_sector_names: list, individual_data: dict) -> str:
        """Build a heuristic narrative that never produces empty placeholders."""
        emo_label = emotion.get('emotion_label', '中性')
        emo_score = emotion.get('emotion_score', 5.0)

        parts = [f"{date} 前瞻：情绪{emo_label}（分数{emo_score}），趋势评分 {round(trend_score, 1)}。"]

        if top_sector_names:
            parts.append(f"重点关注板块：{'、'.join(top_sector_names)}。")
        elif individual_data:
            hot = individual_data.get('hot', [])
            if hot:
                hot_names = [f"{s.get('name', s.get('code', ''))}({s.get('pct_chg', 0):+.1f}%)" for s in hot[:5]]
                parts.append(f"板块数据缺失，强势个股参考：{'、'.join(hot_names)}。")
        else:
            parts.append("板块与个股数据暂缺，建议关注大盘整体情绪。")

        parts.append(f"注意风险：市场波动与消息面驱动风险。")
        return ''.join(parts)

    def _build_data_sources(self, ctx: Dict[str, Any], emotion: Dict[str, Any], sector: Dict[str, Any]) -> Dict[str, Any]:
        """Summarize what data was available to inform the forecast."""
        sources = {
            "market_basic": {
                "available": bool(ctx.get('market_basic')),
                "note": ctx.get('market_basic', {}).get('note', '')
            },
            "emotion_data": {
                "available": bool(ctx.get('emotion_data')),
                "note": ctx.get('emotion_data', {}).get('note', '')
            },
            "sector_data": {
                "available": bool(ctx.get('sector_data', {}).get('top_sectors')),
                "note": ctx.get('sector_data', {}).get('note', '')
            },
            "fund_flow": {
                "available": bool(ctx.get('fund_flow', {}).get('data')),
                "source": ctx.get('fund_flow', {}).get('source', '')
            },
            "macro_news": {
                "available": bool(ctx.get('macro_news', {}).get('items')),
                "count": len(ctx.get('macro_news', {}).get('items', []))
            },
            "tech_index": {
                "available": bool(ctx.get('tech_index')) and ctx.get('tech_index', {}).get('note') != 'no_data',
            },
            "history_benchmark": {
                "available": bool(ctx.get('history_benchmark', {}).get('matches')),
                "matches": ctx.get('history_benchmark', {}).get('matches', 0)
            }
        }
        return sources

    def _derive_risk_tips(self, ctx: Dict[str, Any], emotion: Dict[str, Any], sector: Dict[str, Any]) -> List[str]:
        """Derive risk tips from available data instead of hardcoding."""
        tips = []
        emo_score = emotion.get('emotion_score', 5)
        meta = emotion.get('meta', {})

        # Emotion-based risks
        if emo_score <= 3:
            tips.append("市场情绪偏空，警惕恐慌性抛售")
        elif emo_score >= 7:
            tips.append("市场情绪过热，警惕追高风险")

        # Limit-up/down risks
        limit_up = int(meta.get('limit_up', 0))
        limit_down = int(meta.get('limit_down', 0))
        if limit_down > limit_up * 0.5:
            tips.append("跌停家数偏多，注意流动性风险")
        if limit_up > 100:
            tips.append("涨停家数激增，注意短期过热回调")

        # Breadth risks
        mb = ctx.get('market_basic', {})
        count_up = int(mb.get('count_up', 0))
        count_down = int(mb.get('count_down', 0))
        total = count_up + count_down
        if total > 0:
            up_ratio = count_up / total
            if up_ratio < 0.3:
                tips.append("上涨家数占比过低，市场赚钱效应弱")
            elif up_ratio > 0.7:
                tips.append("普涨格局，注意后续分化风险")

        # Sector concentration risk
        sector_list = sector.get('sector_candidates', [])
        top_sector_count = sum(1 for s in sector_list if s.get('industry'))
        if top_sector_count == 0:
            tips.append("板块数据缺失，建议从个股层面评估风险")

        # Always include these foundational tips
        tips.append("消息面风险")
        tips.append("流动性瞬时波动")

        return tips
