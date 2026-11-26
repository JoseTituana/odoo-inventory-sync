# Point of sale browser test

`e2e/run.sh` checks the till the way the store uses it, in Spanish:

1. starts a throwaway PostgreSQL 16 + Odoo 19 in Docker and creates a database
   with `pos_stock_guard` and Spanish (Ecuador);
2. sets prices with VAT included and imports `examples/catalogo-prueba.xlsx`
   with `odoo-sync` (`e2e/setup.py`), then creates the till "Local Principal"
   with a salesperson (PIN 1111) and a manager (PIN 4321);
3. drives the till with Playwright (`e2e/pos.spec.js`):
   - the salesperson opens the till with a PIN and sells a pair in stock; the
     customer pays the shelf price (VAT inside) and the stock goes down;
   - out of stock: Cancel removes the pair; a wrong PIN is refused; the
     manager's PIN authorizes it, and both attempts are logged;
   - offline: a pair within the last downloaded stock is sold, a second one is
     blocked; back online, the sale reaches Odoo marked "sold offline";
4. fails if Odoo logs an error while the till is used.

```bash
pip install -e .
(cd e2e && npm ci && npx playwright install chromium)
e2e/run.sh            # KEEP=1 e2e/run.sh leaves Odoo running on :8079
```

It runs in CI as the `pos-e2e` job.
