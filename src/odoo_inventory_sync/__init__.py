"""Sync a product CSV with Odoo over XML-RPC."""
from .client import OdooClient, OdooError
from .sync import ProductRow, SyncResult, load_rows, sync_products

__all__ = ["OdooClient", "OdooError", "ProductRow", "SyncResult", "load_rows", "sync_products"]
__version__ = "0.1.0"
