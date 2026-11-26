{
    "name": "POS Stock Guard",
    "summary": "Block point of sale sales of out-of-stock variants unless a manager "
               "authorizes them with a PIN",
    "description": """
When a cashier adds a product variant whose on-hand quantity at the point of
sale's stock location is lower than the quantity on the order, the sale is
blocked. A point of sale manager can authorize it with their employee PIN.
Every authorization and every failed PIN attempt is logged.

Offline, the till checks the last stock it downloaded: sales within it go
through and are logged as "sold offline" when the connection is back; sales
beyond it stay blocked, because a manager PIN cannot be checked offline.
""",
    "version": "19.0.1.1.0",
    "category": "Sales/Point of Sale",
    "license": "LGPL-3",
    "depends": ["point_of_sale", "pos_hr"],
    "data": [
        "security/ir.model.access.csv",
        "views/pos_stock_override_views.xml",
    ],
    "assets": {
        "point_of_sale._assets_pos": [
            "pos_stock_guard/static/src/**/*",
        ],
    },
    "installable": True,
}
