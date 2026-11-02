"""
Seed a demo Tenant and Tenant Admin for instant testing.
Usage:
    cd backend
    ..\.venv\Scripts\python.exe -m app.scripts.seed_demo_tenant
"""
import asyncio
import logging
import uuid
from app.core.database import AsyncSessionLocal
from app.core.security import hash_password
from app.models.tenant import Tenant, TenantStatus
from app.models.user import User, UserRole
from app.models.ai_assistant_config import AIAssistantConfig
from app.models.product import Product
from app.models.customer import Customer
from app.models.conversation import Conversation, Message
from app.models.lead import Lead
from sqlalchemy import select

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")
logger = logging.getLogger(__name__)

async def seed_demo():
    async with AsyncSessionLocal() as db:
        # Check if demo tenant already exists
        stmt = select(Tenant).where(Tenant.slug == "acme-sales")
        tenant = (await db.execute(stmt)).scalar_one_or_none()
        
        if not tenant:
            tenant = Tenant(
                id=uuid.uuid4(),
                name="Acme Sales Corp",
                slug="acme-sales",
                email="admin@acme.com",
                status=TenantStatus.ACTIVE,
                industry="Software & Technology",
            )
            db.add(tenant)
            await db.flush()
            logger.info("Created Tenant: %s (id: %s)", tenant.name, tenant.id)
        else:
            logger.info("Tenant already exists: %s", tenant.name)

        # Check if Tenant Admin exists
        stmt_user = select(User).where(User.email == "tenantadmin@example.com")
        admin_user = (await db.execute(stmt_user)).scalar_one_or_none()

        if not admin_user:
            admin_user = User(
                id=uuid.uuid4(),
                tenant_id=tenant.id,
                first_name="Sarah",
                last_name="Connor",
                email="tenantadmin@example.com",
                password_hash=hash_password("Password123!"),
                role=UserRole.TENANT_ADMIN,
                is_active=True,
                is_verified=True,
            )
            db.add(admin_user)
            await db.flush()
            logger.info("Created Tenant Admin user: tenantadmin@example.com / Password123!")
        else:
            logger.info("Tenant Admin user already exists: %s", admin_user.email)

        # A demo sales rep, so the rep workspace can be tried right away
        rep = (await db.execute(select(User).where(User.email == "salesrep@example.com"))).scalar_one_or_none()
        if not rep:
            db.add(User(
                id=uuid.uuid4(),
                tenant_id=tenant.id,
                first_name="Priya",
                last_name="Sharma",
                email="salesrep@example.com",
                password_hash=hash_password("Password123!"),
                role=UserRole.SALES_USER,
                is_active=True,
                is_verified=True,
                is_available=True,
            ))
            logger.info("Created Sales User: salesrep@example.com / Password123!")

        # Ensure AIAssistantConfig exists
        stmt_cfg = select(AIAssistantConfig).where(AIAssistantConfig.tenant_id == tenant.id)
        cfg = (await db.execute(stmt_cfg)).scalar_one_or_none()
        if not cfg:
            cfg = AIAssistantConfig(
                tenant_id=tenant.id,
                assistant_name="Aria - Senior Sales Assistant",
                personality="Consultative & Helpful",
                custom_instructions="You are an expert sales consultant for Acme Sales Corp. Help prospects understand pricing, features, and answer questions clearly.",
                greeting_message="Hi! Welcome to Acme Sales Corp. How can I help you accelerate your sales today?",
                max_discount_percent=15.0,
                groq_model="llama-3.3-70b-versatile",
            )
            db.add(cfg)
            logger.info("Created default AI Assistant configuration.")

        # Seed sample products if none exist
        stmt_prod = select(Product).where(Product.tenant_id == tenant.id)
        prods = (await db.execute(stmt_prod)).scalars().all()
        if not prods:
            p1 = Product(
                tenant_id=tenant.id,
                name="OmniCRM Cloud Enterprise",
                sku="CRM-ENT-01",
                category="Software",
                description="All-in-one sales CRM with automated deal pipelines, custom reporting, and email sync.",
                price=1500.0,
                currency="USD",
                features="Pipeline tracking, automated email sequences, custom dashboards, 50 user seats",
                is_active=True,
            )
            p2 = Product(
                tenant_id=tenant.id,
                name="AI Lead Intelligence Booster",
                sku="AI-BOOST-02",
                category="Add-on",
                description="Real-time buyer qualification and conversational AI add-on for OmniCRM.",
                price=499.0,
                currency="USD",
                features="24/7 autonomous lead qualification, CRM auto-sync, WhatsApp channel integration",
                is_active=True,
            )
            db.add_all([p1, p2])
            logger.info("Created demo products.")

        # Seed sample Customer & Lead if none exist
        stmt_cust = select(Customer).where(Customer.tenant_id == tenant.id)
        custs = (await db.execute(stmt_cust)).scalars().all()
        if not custs:
            cust = Customer(
                tenant_id=tenant.id,
                name="Jordan Smith",
                phone="+1 (555) 987-6543",
                email="jordan@apextech.io",
            )
            db.add(cust)
            await db.flush()

            conv = Conversation(
                tenant_id=tenant.id,
                customer_id=cust.id,
                channel="WEB_SIMULATOR",
                status="ACTIVE_AI",
                detected_intent="pricing_inquiry",
            )
            db.add(conv)
            await db.flush()

            m1 = Message(
                conversation_id=conv.id,
                sender_type="CUSTOMER",
                content="Hi, we are looking for a CRM solution for 40 sales reps. What is your pricing for OmniCRM?",
            )
            m2 = Message(
                conversation_id=conv.id,
                sender_type="AI",
                content="Hello Jordan! OmniCRM Cloud Enterprise is $1,500/year and includes 50 user seats, pipeline tracking, and email automation. Does this fit your timeline?",
            )
            db.add_all([m1, m2])

            lead = Lead(
                tenant_id=tenant.id,
                customer_id=cust.id,
                conversation_id=conv.id,
                title="Opportunity - Jordan Smith (Apex Tech)",
                status="QUALIFIED",
                score=88,
                score_reason="High purchase intent with 40 seats requirement and immediate deployment timeline.",
                requirements="OmniCRM Enterprise for 40 sales reps",
                budget=1500.0,
                timeline="Within 30 days",
                priority="HOT",
                source="WEB_SIMULATOR",
            )
            db.add(lead)
            logger.info("Created demo customer, conversation, and scored lead.")

        await db.commit()
        logger.info("Seeding complete! You can now log in with:")
        logger.info("Email:    tenantadmin@example.com")
        logger.info("Password: Password123!")

if __name__ == "__main__":
    asyncio.run(seed_demo())
