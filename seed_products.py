import asyncio
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("seed_products")


async def main():
    from stripe_client import get_stripe_client

    stripe_mod = await get_stripe_client()

    existing = stripe_mod.Product.search(query="name:'Evil Apples 4D Pro'")
    if existing.data:
        logger.info(f"Product already exists: {existing.data[0].id}")
        prices = stripe_mod.Price.list(product=existing.data[0].id, active=True)
        for p in prices.data:
            logger.info(f"  Price: {p.id} - ${p.unit_amount/100}/{p.recurring.interval if p.recurring else 'one-time'}")
        return

    product = stripe_mod.Product.create(
        name="Evil Apples 4D Pro",
        description="Unlimited games, tournaments, and premium AI card packs for your Discord server.",
        metadata={
            "app": "evil_apples_4d",
            "tier": "pro",
        },
    )
    logger.info(f"Created product: {product.id}")

    monthly_price = stripe_mod.Price.create(
        product=product.id,
        unit_amount=1900,
        currency="usd",
        recurring={"interval": "month"},
        metadata={"plan": "pro_monthly"},
    )
    logger.info(f"Created monthly price: {monthly_price.id} ($19/month)")

    yearly_price = stripe_mod.Price.create(
        product=product.id,
        unit_amount=4999,
        currency="usd",
        recurring={"interval": "year"},
        metadata={"plan": "pro_yearly"},
    )
    logger.info(f"Created yearly price: {yearly_price.id} ($49.99/year)")

    print(f"\n{'='*50}")
    print(f"PRODUCT CREATED: {product.id}")
    print(f"MONTHLY PRICE ID: {monthly_price.id}")
    print(f"YEARLY PRICE ID: {yearly_price.id}")
    print(f"\nSet STRIPE_PRICE_ID={monthly_price.id} in your environment.")
    print(f"{'='*50}")


if __name__ == "__main__":
    asyncio.run(main())
