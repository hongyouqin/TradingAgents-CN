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
    """Analyze sector rotation and prioritize sectors for next day."""

    async def run(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        sector_data = ctx.get('sector_data', {})
        top_sectors: List[Dict[str, Any]] = sector_data.get('top_sectors') or []

        prioritized: List[Dict[str, Any]] = []
        for s in top_sectors[:20]:
            score = 0.0
            try:
                score += float(s.get('sum_amount', 0)) / (1e8 + 1)  # normalize by large number
                score += float(s.get('avg_pct', 0)) * 2.0
            except Exception:
                pass
            prioritized.append({"industry": s.get('industry'), "score": round(score, 4), "count": int(s.get('count', 0)), "avg_pct": s.get('avg_pct')})

        # sort descending
        prioritized.sort(key=lambda x: x['score'], reverse=True)

        # return top 6 as candidate main-lines
        return {"sector_candidates": prioritized[:6], "note": "heuristic_priority"}


class OutputAgent:
    """Integrate all agent outputs into a final forecast and human readable text.

    The output includes machine-readable fields and a short narrative. Replace or
    extend the narrative generation with an LLM call when available.
    """

    async def run(self, ctx: Dict[str, Any], emotion: Dict[str, Any], sector: Dict[str, Any], user: dict = None) -> Dict[str, Any]:
        date = ctx.get('date') or datetime.utcnow().date().isoformat()
        trend_score = 50.0 + (emotion.get('emotion_score', 5.0) - 5.0) * 5.0
        trend_score = max(0.0, min(100.0, trend_score))

        sector_list = sector.get('sector_candidates', [])
        top_sector_names = [s['industry'] for s in sector_list if s.get('industry')]

        # Build a concise prompt for the LLM
        try:
            from app.services.forecast_llm_service import call_llm_with_billing
            import json

            prompt_obj = {
                "date": date,
                "market_basic": ctx.get('market_basic', {}),
                "emotion": emotion,
                "sectors": sector_list,
                "top_sectors": top_sector_names
            }
            prompt = (
                "请基于以下结构化市场数据，生成明日市场前瞻（中文）:\n" + json.dumps(prompt_obj, ensure_ascii=False)
                + "\n\n输出要求：\n1) 给出趋势评分（0-100）；\n2) 一段不超过120字的开盘预判（narrative）；\n3) 简要策略建议；\n4) 风险提示列表。\n只返回纯文本叙述（不必返回JSON）。"
            )

            success, llm_response, billing = await call_llm_with_billing(user, prompt, model_name=None, max_output_tokens=400)

            if success and llm_response:
                narrative = llm_response.strip()
            else:
                # fallback to heuristic narrative
                narrative = f"{date} 前瞻：情绪{emotion.get('emotion_label')}（分数{emotion.get('emotion_score')}），趋势评分 {round(trend_score,1)}。重点关注板块：{', '.join(top_sector_names)}。注意风险：市场波动与消息面驱动风险。"
        except Exception:
            narrative = f"{date} 前瞻：情绪{emotion.get('emotion_label')}（分数{emotion.get('emotion_score')}），趋势评分 {round(trend_score,1)}。重点关注板块：{', '.join(top_sector_names)}。注意风险：市场波动与消息面驱动风险。"

        forecast = {
            "date": date,
            "market_forecast": {
                "trend_score": round(trend_score, 2),
                "narrative": narrative
            },
            "emotion_forecast": emotion,
            "sector_forecast": sector_list,
            "strategy_suggest": {
                "short": "控制仓位，关注强势板块" if emotion.get('emotion_score', 5) < 7 else "优选高质量趋势股，关注回撤"},
            "risk_tips": ["消息面风险", "流动性瞬时波动"],
            "agent_log": {
                "generated_at": datetime.utcnow(),
                "components": {"aggregator": True, "emotion": True, "sector": True, "output": True}
            }
        }

        return forecast
