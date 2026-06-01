from .data_lookup import  search_data
from .calculator import calculate
from .ecommerce import check_stock, get_discount, calc_shipping, get_price
from .data_lookup import search_data
DATA_TOOLS = [
    {
        "name": "search_data",
        "description": "Search and read local PDF/data files in the data folder. Always use this for every user question.",
        "function": search_data,
    },
]
TOOLS = [
    {
        "name": "calculate",
        "description": "Evaluate a basic arithmetic expression. Input example: 1999 * 2 + 25",
        "function": calculate,
    },
    {
        "name": "get_price",
        "description": "Return the unit price in USD for a product name. Input example: iphone",
        "function": get_price,
    },
    {
        "name": "check_stock",
        "description": "Return the available stock quantity for a product name. Input example: iphone",
        "function": check_stock,
    },
    {
        "name": "get_discount",
        "description": "Return discount percent for a coupon code. Input example: WINNER",
        "function": get_discount,
    },
    {
        "name": "calc_shipping",
        "description": "Calculate shipping cost. Input format: weight_kg, destination. Example: 1.2, Hanoi",
        "function": calc_shipping,
    },
    {
    "name": "search_data",
    "description": "Search local files in the data folder. Use this before answering questions about uploaded/local data. Input example: iphone warranty policy",
    "function": search_data,
},

]
