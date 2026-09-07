"""SEAL Gateway — multi-platform channel adapters.

Each channel (Telegram, Discord, Slack, WhatsApp, etc) implements GatewayChannel
from base.py. Channels handle inbound message routing to SEAL agents and
outbound delivery from agents/cron jobs back to the platform.
"""

from .base import GatewayChannel, InboundMessage, OutboundMessage, GatewayError
from .multi_model import (
    ModelAdapter,
    ModelError,
    ModelResponse,
    ChatMessage,
    OllamaAdapter,
    NvidiaNIMAdapter,
    XAIAdapter,
    GeminiAdapter,
    LMStudioAdapter,
    OpenAIAdapter,
    BedrockAdapter,
    MultiModelRouter,
)
from .cron_delivery import CronDeliveryHook, DeliveryConfig, DeliveryResult, DeliveryError
from .webhooks import WebhookServer, WebhookEvent, WebhookError, verify_signature
from .shell_hooks import ShellHookRegistry, HookConfig, HookResult, HookError
from .provider_fallback import ProviderFallbackPool

__all__ = [
    "GatewayChannel", "InboundMessage", "OutboundMessage", "GatewayError",
    "ModelAdapter", "ModelError", "ModelResponse", "ChatMessage",
    "OllamaAdapter", "NvidiaNIMAdapter", "XAIAdapter", "GeminiAdapter", "LMStudioAdapter",
    "OpenAIAdapter", "BedrockAdapter",
    "MultiModelRouter",
    "CronDeliveryHook", "DeliveryConfig", "DeliveryResult", "DeliveryError",
    "WebhookServer", "WebhookEvent", "WebhookError", "verify_signature",
    "ShellHookRegistry", "HookConfig", "HookResult", "HookError",
    "ProviderFallbackPool",
]
