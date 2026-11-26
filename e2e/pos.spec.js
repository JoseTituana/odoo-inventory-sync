// Browser test of the till with pos_stock_guard, in Spanish, as the store uses it.
// Run through e2e/run.sh, which prepares the database and fixtures.json.
const { test, expect } = require("@playwright/test");
const fx = require("./fixtures.json");

test.describe.configure({ mode: "serial" });

/** @type {import('@playwright/test').Page} */
let page;
let context;

// JSON-RPC with the browser's own session (the admin user).
async function rpc(model, method, args, kwargs = {}) {
    const res = await page.request.post(`${fx.url}/web/dataset/call_kw/${model}/${method}`, {
        data: { jsonrpc: "2.0", method: "call", params: { model, method, args, kwargs } },
    });
    const body = await res.json();
    if (body.error) {
        throw new Error(JSON.stringify(body.error.data?.message || body.error));
    }
    return body.result;
}

async function stock(product) {
    const [p] = await rpc("product.product", "read", [[product.id], ["qty_available"]]);
    return p.qty_available;
}

async function overrides() {
    return rpc("pos.stock.override", "search_read", [[]], {
        fields: ["product_id", "employee_id", "success", "offline", "qty_requested", "qty_available"],
        order: "id",
    });
}

// A USB barcode reader types the digits fast and presses Enter.
async function scan(product) {
    await page.keyboard.type(product.barcode, { delay: 5 });
    await page.keyboard.press("Enter");
}

const lines = () => page.locator(".order-container .orderline");
const dialog = () => page.locator(".modal-dialog").last();

async function typePinOnPad(pin) {
    for (const digit of pin) {
        await dialog().locator("button", { hasText: new RegExp(`^${digit}$`) }).first().click();
    }
    await dialog().locator("button.btn-primary").last().click();
}

async function payCash({ offline = false } = {}) {
    await page.getByRole("button", { name: "Pago", exact: true }).click();
    await page.locator(".paymentmethod", { hasText: "Efectivo" }).first().click();
    await page.getByRole("button", { name: /Validar/ }).first().click();
    if (offline) {
        // Odoo's own notice when the order cannot be sent yet.
        await page.getByRole("button", { name: "Continuar con funcionalidad limitada" }).click();
    }
    await page.getByRole("button", { name: /Nueva orden/ }).first().click();
    await expect(lines()).toHaveCount(0);
}

test.beforeAll(async ({ browser }) => {
    context = await browser.newContext();
    page = await context.newPage();
    await page.goto(`${fx.url}/web/login`);
    await page.fill("input[name=login]", "admin");
    await page.fill("input[name=password]", "admin");
    await page.click("button[type=submit]");
    await page.waitForURL(/\/odoo/);
});

test.afterAll(async () => {
    await context?.close();
});

test("el vendedor abre la caja con su PIN y vende un par con stock, IVA incluido", async () => {
    await page.goto(`${fx.url}/pos/ui?config_id=${fx.config_id}`);
    await page.getByText(/Abrir caja|Desbloquear/).first().click();
    await page.locator("input.form-control").first().fill("1111");
    await page.keyboard.press("Enter");
    // First opening of the day: opening cash.
    await page.getByRole("button", { name: "Abrir caja registradora" }).click();

    await scan(fx.in_stock);
    await expect(lines()).toHaveCount(1);
    // The shelf price is what the customer pays: VAT is inside it, not added.
    await expect(page.locator(".order-summary")).toContainText("48,00");

    await payCash();
    await expect.poll(() => stock(fx.in_stock)).toBe(4);
    const [order] = await rpc("pos.order", "search_read", [[]], {
        fields: ["amount_total", "amount_tax", "state"], order: "id desc", limit: 1,
    });
    expect(order.amount_total).toBeCloseTo(48, 2);
    expect(order.amount_tax).toBeCloseTo(6.26, 2);
});

test("sin stock: cancelar quita el par de la orden", async () => {
    await scan(fx.no_stock);
    await expect(dialog()).toContainText("No hay stock suficiente");
    await dialog().getByRole("button", { name: "Cancelar" }).click();
    await expect(lines()).toHaveCount(0);
});

test("sin stock: un PIN equivocado no autoriza y el del encargado sí", async () => {
    await scan(fx.no_stock);
    await expect(dialog()).toContainText("No hay stock suficiente");
    await dialog().getByRole("button", { name: "Ingresar PIN del encargado" }).click();
    await typePinOnPad("9999");
    await expect(dialog()).toContainText("No autorizado");
    await dialog().locator("button.btn-primary").click();
    await expect(lines()).toHaveCount(0);

    await scan(fx.no_stock);
    await dialog().getByRole("button", { name: "Ingresar PIN del encargado" }).click();
    await typePinOnPad("4321");
    await expect(page.getByText("Autorizado por Encargado")).toBeVisible();
    await expect(lines()).toHaveCount(1);

    // Payment does not ask again for the quantity already authorized.
    await payCash();
    const log = await overrides();
    expect(log.map((o) => [o.success, o.employee_id && o.employee_id[1]])).toEqual([
        [false, false],
        [true, "Encargado"],
    ]);
    await expect.poll(() => stock(fx.no_stock)).toBe(-1);
});

test("sin conexión: vende dentro del último stock y bloquea lo que pasa de él", async () => {
    await context.setOffline(true);
    await scan(fx.one_left);
    await expect(lines()).toHaveCount(1);

    await scan(fx.one_left); // a second pair: only 1 was in stock
    await expect(dialog()).toContainText("sin conexión");
    await dialog().locator("button.btn-primary").click();
    await expect(lines().first()).not.toContainText("2,00");

    await payCash({ offline: true });
    await context.setOffline(false);

    // Back online: the sale reaches Odoo and is listed as sold offline.
    await expect.poll(async () => (await overrides()).filter((o) => o.offline).length, {
        timeout: 60_000,
    }).toBe(1);
    await expect.poll(() => stock(fx.one_left), { timeout: 60_000 }).toBe(0);
});
