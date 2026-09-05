"""Cloudflare Workers AI client. Mock-by-default so n8n has an endpoint immediately.

Set CLOUDFLARE_ACCOUNT_ID + CLOUDFLARE_API_TOKEN to call the real model.
Otherwise returns a deterministic local reasoning stub (inventory math).
"""
from __future__ import annotations

import os

import httpx

MODEL = os.environ.get("CLOUDFLARE_MODEL", "@cf/meta/llama-3.1-8b-instruct")


def _mock_reason(event_type: str, entity: str, payload: dict) -> dict:
    if event_type == "inventory_check":
        stock = float(payload.get("stock", 0))
        avg = float(payload.get("avg_daily_sales", 1) or 1)
        days = stock / avg if avg else 0
        low = days < 3
        return {
            "summary": f"{entity} có nguy cơ hết hàng trong ~{days:.1f} ngày" if low else f"{entity} tồn kho ổn ({days:.1f} ngày)",
            "reasoning": f"Stock {stock:g} / avg_sales {avg:g}/ngày -> stock_days ~{days:.1f}, ngưỡng cảnh báo 3 ngày",
            "confidence": 0.91 if low else 0.75,
            "action_type": "TICKET" if low else "REPORT",
            "recommended_action": f"restock_{200 if low else 0}",
            "ticket_type": "inventory_risk" if low else "general",
        }
    if event_type == "telegram_chat":
        # Fallback khi chưa cấu hình Cloudflare creds: rule keyword rõ ràng,
        # vẫn đi qua Policy Engine như mọi decision khác.
        text = str(payload.get("text", "")).lower()
        if any(k in text for k in ("tồn kho", "hết hàng", "nhập hàng", "restock", "còn hàng", "kho còn", "kiểm kho", "hàng còn")):
            return {
                "summary": "Shop còn 15 sản phẩm, em đã tạo phiếu nhập thêm 200 nhé!",
                "reasoning": "Mock telegram: từ khóa tồn kho -> TICKET restock_200",
                "confidence": 0.8,
                "action_type": "TICKET",
                "recommended_action": "restock_200",
                "ticket_type": "inventory_risk",
            }
        if any(k in text for k in ("báo cáo", "report", "tổng hợp", "thống kê")):
            return {
                "summary": "Báo cáo hôm nay: tồn kho ổn, 0 ticket mới. Em đã ghi vào Google Sheet nhé!",
                "reasoning": "Mock telegram: yêu cầu báo cáo -> REPORT",
                "confidence": 0.8,
                "action_type": "REPORT",
                "recommended_action": "",
                "ticket_type": "general",
            }
        if any(k in text for k in ("hoàn tiền", "refund", "đổi giá", "giá")):
            return {
                "summary": "Yêu cầu này cần anh/chị duyệt trước khi em thực hiện nhé!",
                "reasoning": "Mock telegram: refund/pricing -> EXECUTE (Policy Engine sẽ bắt approval)",
                "confidence": 0.8,
                "action_type": "EXECUTE",
                "recommended_action": "refund_order_1",
                "ticket_type": "general",
            }
        return {
            "summary": f"Em đã nhận được tin nhắn của anh/chị! Anh/chị cần em kiểm tra tồn kho, làm báo cáo hay xử lý việc gì ạ?",
            "reasoning": "Mock telegram: small-talk -> NOTIFY (reply)",
            "confidence": 0.7,
            "action_type": "NOTIFY",
            "recommended_action": "",
            "ticket_type": "general",
        }
    return {
        "summary": f"Event {event_type} cho {entity} đã được ghi nhận",
        "reasoning": "Mock reasoning: chưa có rule chuyên biệt, mặc định REPORT để n8n route tiếp",
        "confidence": 0.5,
        "action_type": "REPORT",
        "recommended_action": "",
        "ticket_type": "general",
    }


async def reason(event_type: str, entity: str, payload: dict) -> dict:
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "")
    if not account or not token:
        return _mock_reason(event_type, entity, payload)
    url = f"https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{MODEL}"
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            json={"messages": [
                {"role": "system", "content": (
                    "You are DailyOps, a helpful ops assistant speaking Vietnamese. "
                    "Reply with PURE JSON only, no markdown fences: "
                    '{"summary": string (Vietnamese), "reasoning": string, '
                    '"confidence": number 0-1, '
                    '"action_type": one of REPORT|NOTIFY|TICKET|EXECUTE, '
                    '"recommended_action": string (e.g. restock_200, refund_order_1, or empty), '
                    '"ticket_type": string, '
                    '"reply_text": string (Vietnamese message for the end user; used as the reply when the event comes from chat)}. '
                    "Rules: chao hoi / tam su -> NOTIFY; xin bao cao / tong hop -> REPORT; "
                    "ton kho / het hang / nhap hang -> TICKET with recommended_action restock_<qty>; "
                    "hoan tien / doi gia / can thiep he thong -> EXECUTE."
                )},
                {"role": "user", "content": f"event={event_type} entity={entity} payload={payload}"},
            ]},
        )
        r.raise_for_status()
        data = r.json()
    # Best-effort unwrap; fall back to mock shape on unexpected responses.
    try:
        text = data["result"]["response"]
        import json as _json
        parsed = _json.loads(text) if isinstance(text, str) else {}
        return {**_mock_reason(event_type, entity, payload), **parsed}
    except Exception:
        return _mock_reason(event_type, entity, payload)
