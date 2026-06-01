PRODUCTS = {
    "iphone": {"price": 999.0, "stock": 5, "weight_kg": 0.25},
    "airpods": {"price": 179.0, "stock": 12, "weight_kg": 0.08},
    "macbook": {"price": 1499.0, "stock": 3, "weight_kg": 1.4},
}

COUPONS = {
    "WINNER": 10,
    "STUDENT": 15,
    "FREESHIP": 0,
}

DESTINATION_BASE_SHIPPING = {
    "hanoi": 5.0,
    "ho chi minh": 6.0,
    "danang": 7.0,
}


def _normalize(value: str) -> str:
    return str(value).strip().lower()


def get_price(item_name: str) -> str:
    item = PRODUCTS.get(_normalize(item_name))
    if not item:
        return f"PRODUCT_NOT_FOUND: {item_name}"
    return str(item["price"])


def check_stock(item_name: str) -> str:
    item = PRODUCTS.get(_normalize(item_name))
    if not item:
        return f"PRODUCT_NOT_FOUND: {item_name}"
    return str(item["stock"])


def get_discount(coupon_code: str) -> str:
    return str(COUPONS.get(str(coupon_code).strip().upper(), 0))


def calc_shipping(input_text: str) -> str:
    try:
        weight_text, destination = str(input_text).split(",", 1)
        weight_kg = float(weight_text.strip())
        base = DESTINATION_BASE_SHIPPING.get(_normalize(destination), 10.0)
        return str(round(base + weight_kg * 2.0, 2))
    except Exception:
        return "SHIPPING_ERROR: expected input format 'weight_kg, destination'"
