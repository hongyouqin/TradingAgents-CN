"""Funds management tool adapter - connect to paper trading / account collections.

Implements:
 - query_position: read paper_positions by user_id
 - rebalance: create a rebalance request document for downstream processing
"""

from typing import Dict, Any, List
from datetime import datetime

from fastapi import HTTPException

from app.core.database import get_mongo_db


class FundsTool:
    async def query_position(self, account_id: str) -> Dict[str, Any]:
        """Query positions for a given user/account from paper_positions collection.

        account_id is expected to be the user_id used in paper_positions.user_id.
        """
        db = get_mongo_db()
        positions = await db["paper_positions"].find({"user_id": account_id}).to_list(length=None)
        return {"account_id": account_id, "positions": positions}

    async def rebalance(self, account_id: str, strategy: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a rebalance by placing market orders using the internal paper.place_order logic.

        Args:
            account_id: user_id string for whom to execute orders
            strategy: dict or list describing orders. Expected format:
                {"orders": [{"code": "000001", "side": "buy", "quantity": 100, "market": "CN"}, ...]}

        Returns:
            Dict with per-order results.
        """
        results = []
        db = get_mongo_db()

        # Try to import internal place_order and PlaceOrderRequest
        try:
            from app.routers.paper import place_order, PlaceOrderRequest
        except Exception:
            # Fallback: record request for downstream processing
            req = {
                "account_id": account_id,
                "strategy": strategy,
                "status": "pending",
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow()
            }
            res = await db["paper_rebalance_requests"].insert_one(req)
            req["_id"] = str(res.inserted_id)
            return {"request": req, "note": "place_order not importable; saved request"}

        orders = []
        if isinstance(strategy, dict) and "orders" in strategy:
            orders = strategy["orders"]
        elif isinstance(strategy, list):
            orders = strategy
        else:
            raise ValueError("Invalid strategy format: expected dict with 'orders' or a list")

        # Attempt to fetch username for better audit info
        user_doc = await db["users"].find_one({"_id": account_id})
        username = user_doc.get("username") if user_doc else str(account_id)

        for o in orders:
            try:
                payload = PlaceOrderRequest(
                    code=o.get("code") or o.get("symbol"),
                    side=o.get("side"),
                    quantity=int(o.get("quantity")),
                    market=o.get("market")
                )
            except Exception as e:
                results.append({"order": o, "status": "error", "error": f"invalid order payload: {e}"})
                continue

            current_user = {"id": account_id, "username": username}

            try:
                # place_order is an async endpoint function; call it directly
                resp = await place_order(payload, current_user)
                results.append({"order": o, "status": "ok", "response": resp})
            except HTTPException as he:
                results.append({"order": o, "status": "error", "error": str(he.detail)})
            except Exception as e:
                results.append({"order": o, "status": "error", "error": str(e)})

        return {"account_id": account_id, "results": results}
