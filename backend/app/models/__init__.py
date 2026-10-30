"""
Models package — import all models here so Alembic autogeneration can discover them.
"""
from app.models.tenant import Tenant
from app.models.user import User, UserRole
from app.models.subscription_plan import SubscriptionPlan, BillingCycle
from app.models.billing_cycle import BillingCycleModel
from app.models.audit_log import AuditLog, AuditLogAction, AuditLogResourceType
from app.models.ai_assistant_config import AIAssistantConfig
from app.models.product import Product
from app.models.knowledge import KnowledgeDocument, KnowledgeChunk
from app.models.customer import Customer
from app.models.conversation import Conversation, Message
from app.models.lead import Lead
from app.models.ai_usage_log import AIUsageLog
from app.models.notification import Notification
from app.models.follow_up import FollowUpSchedule
from app.models.inventory import Warehouse, ProductInventory

__all__ = [
    "Tenant",
    "User",
    "UserRole",
    "SubscriptionPlan",
    "BillingCycle",
    "BillingCycleModel",
    "AuditLog",
    "AuditLogAction",
    "AuditLogResourceType",
    "AIAssistantConfig",
    "Product",
    "KnowledgeDocument",
    "KnowledgeChunk",
    "Customer",
    "Conversation",
    "Message",
    "Lead",
    "AIUsageLog",
    "Notification",
    "FollowUpSchedule",
    "Warehouse",
    "ProductInventory",
]
