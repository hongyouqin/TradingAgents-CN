import json
import logging
import asyncio
import uuid
from decimal import Decimal, ROUND_HALF_UP
from typing import Tuple, Optional, Dict, Any

from app.core.unified_config import unified_config
from app.services.simple_analysis_service import get_provider_by_model_name_sync
from tradingagents.graph.trading_graph import create_llm_by_provider
from app.services.power_account_service import power_account_service
from app.models.user import User

logger = logging.getLogger(__name__)


def _estimate_tokens(text: str) -> int:
    # rough approximation: 4 chars per token
    return max(1, len(text) // 4)


def _load_pricing() -> list:
    try:
        path = unified_config.paths.pricing_json
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load pricing.json: {e}")
        return []


def _find_pricing_for_model(model_name: str) -> Optional[Dict[str, Any]]:
    pricing = _load_pricing()
    for p in pricing:
        if p.get('model_name') == model_name:
            return p
    return None


async def call_llm_with_billing(
    user: Optional[dict],
    prompt: str,
    model_name: Optional[str] = None,
    max_output_tokens: int = 400,
    temperature: float = 0.3,
    timeout: int = 180
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    Call LLM for forecast generation and handle billing:
    - If `user` is None, call LLM without billing (system run)
    - If `user` is provided (dict or User), attempt to freeze cost before call and confirm/ cancel after

    Returns (success, response_text, billing_info)
    billing_info contains keys: {cost, order_no, frozen, confirmed}
    """
    try:
        # Normalize user to User model when possible
        user_obj = None
        is_member = False
        if user:
            try:
                if isinstance(user, User):
                    user_obj = user
                elif isinstance(user, dict):
                    # pydantic will validate/convert
                    user_obj = User(**user)
                else:
                    user_obj = User(**user.__dict__)
            except Exception:
                user_obj = None

            # membership flag fallback
            if isinstance(user, dict):
                is_member = bool(user.get('is_member') or user.get('is_vip') or user.get('is_admin', False))
            elif user_obj:
                is_member = getattr(user_obj, 'is_admin', False)

        # decide model
        model = model_name or unified_config.get_deep_analysis_model() or unified_config.get_default_model()

        # find provider and backend
        provider_info = get_provider_by_model_name_sync(model)
        provider = provider_info.get('provider')
        backend = provider_info.get('backend_url')
        api_key = provider_info.get('api_key')

        # pricing
        pricing = _find_pricing_for_model(model)
        input_tokens = _estimate_tokens(prompt)
        output_tokens = max_output_tokens
        input_price = Decimal(str(pricing.get('input_price_per_1k', 0))) if pricing else Decimal('0')
        output_price = Decimal(str(pricing.get('output_price_per_1k', 0))) if pricing else Decimal('0')

        cost = (Decimal(input_tokens) / Decimal(1000) * input_price) + (Decimal(output_tokens) / Decimal(1000) * output_price)
        # round to 4 decimals
        cost = cost.quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)

        billing_info: Dict[str, Any] = {"cost": float(cost), "order_no": None, "frozen": False, "confirmed": False}

        # if user absent or member, skip billing
        if (not user_obj) or is_member:
            logger.info(f"LLM call (no billing) model={model} provider={provider} tokens~{input_tokens}+{output_tokens}")
            llm = create_llm_by_provider(provider=provider, model=model, backend_url=backend, temperature=temperature, max_tokens=max_output_tokens, timeout=timeout, api_key=api_key)
            loop = asyncio.get_event_loop()
            if hasattr(llm, 'ainvoke'):
                response = await llm.ainvoke(prompt)
            else:
                response = await loop.run_in_executor(None, llm.invoke, prompt)

            if hasattr(response, 'content'):
                content = response.content
            elif isinstance(response, str):
                content = response
            else:
                content = str(response)

            return True, content, billing_info

        # otherwise, attempt to freeze
        order_no = f"forecast_llm:{str(uuid.uuid4())}"
        billing_info['order_no'] = order_no

        # freeze
        ok, msg = await power_account_service.freeze(user_obj, order_no, cost, description="Forecast LLM call", metadata={"model": model})
        billing_info['frozen'] = ok
        if not ok:
            logger.warning(f"Freeze failed for user {getattr(user_obj,'username',None)}: {msg}")
            return False, f"freeze_failed:{msg}", billing_info

        # call llm
        try:
            llm = create_llm_by_provider(provider=provider, model=model, backend_url=backend, temperature=temperature, max_tokens=max_output_tokens, timeout=timeout, api_key=api_key)
            loop = asyncio.get_event_loop()
            if hasattr(llm, 'ainvoke'):
                response = await llm.ainvoke(prompt)
            else:
                response = await loop.run_in_executor(None, llm.invoke, prompt)

            if hasattr(response, 'content'):
                content = response.content
            elif isinstance(response, str):
                content = response
            else:
                content = str(response)

            # confirm consume
            okc, msgc = await power_account_service.confirm_consume(order_no)
            billing_info['confirmed'] = okc
            if not okc:
                logger.warning(f"Confirm consume failed for order {order_no}: {msgc}")

            return True, content, billing_info

        except Exception as e:
            # cancel freeze
            await power_account_service.cancel_consume(order_no, reason=str(e))
            billing_info['confirmed'] = False
            logger.error(f"LLM call failed after freeze, cancelled: {e}")
            return False, f"llm_call_failed:{e}", billing_info

    except Exception as e:
        logger.exception(f"Unexpected error in call_llm_with_billing: {e}")
        return False, str(e), {"cost": 0, "order_no": None, "frozen": False, "confirmed": False}
