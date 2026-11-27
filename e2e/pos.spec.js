// Browser test of the till with pos_stock_guard, in Spanish, as the store uses it.
// Run through e2e/run.sh, which prepares the database and fixtures.json.
const { test, expect, request } = require("@playwright/test");
const fx = require("./fixtures.json");

test.describe.configure({ mode: "serial" });

/** @type {import('@playwright/test').Page} */
let page;
let context;
let admin;

// JSON-RPC as the administrator, to check what the till did. The till itself
// runs as "caja", a user with only Point of Sale: User rights.
async function rpc(model, method, args, kwargs = {}) {
    const res = await admin.post(`${fx.url}/web/dataset/call_kw/${model}/${method}`, {
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
async function scan(product, on = page) {
    await on.keyboard.type(product.barcode, { delay: 5 });
    await on.keyboard.press("Enter");
}

const lines = (on = page) => on.locator(".order-container .orderline");
const dialog = (on = page) => on.locator(".modal-dialog").last();

async function login(on) {
    await on.goto(`${fx.url}/web/login`);
    await on.fill("input[name=login]", fx.till_login);
    await on.fill("input[name=password]", fx.till_password);
    await on.click("button[type=submit]");
    await on.waitForURL(/\/odoo/);
}

async function typePinOnPad(pin) {
    for (const digit of pin) {
        await dialog().locator("button", { hasText: new RegExp(`^${digit}$`) }).first().click();
    }
    await dialog().locator("button.btn-primary").last().click();
}

async function payCash({ offline = false, onPaymentScreen = false } = {}) {
    if (!onPaymentScreen) {
        await page.getByRole("button", { name: "Pago", exact: true }).click();
    }
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
    admin = await request.newContext();
    const auth = await admin.post(`${fx.url}/web/session/authenticate`, {
        data: { jsonrpc: "2.0", method: "call", params: { db: fx.db, login: "admin", password: "admin" } },
    });
    expect((await auth.json()).result).toBeTruthy();
    context = await browser.newContext();
    page = await context.newPage();
    await login(page);
});

test.afterAll(async () => {
    await context?.close();
    await admin?.dispose();
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

test("una cantidad escrita en el teclado se revisa al pulsar Pago", async () => {
    await scan(fx.in_stock); // 4 left after the first sale
    await expect(lines()).toHaveCount(1);
    // Odoo groups keys that arrive within ~100 ms as one barcode scan; a numpad
    // press that fast after a scan joins it. A cashier is never that fast.
    await page.waitForTimeout(500);
    await page.locator(".numpad button", { hasText: /^Cant\.$/ }).click();
    await page.locator(".numpad button", { hasText: /^9$/ }).click();
    await expect.poll(() => page.evaluate(() => posmodel.getOrder().lines[0].getQuantity())).toBe(9);

    await page.getByRole("button", { name: "Pago", exact: true }).click();
    await expect(dialog()).toContainText("No hay stock suficiente");
    await expect(dialog()).toContainText("necesita 9");
    await dialog().getByRole("button", { name: "Cancelar" }).click();
    // Still on the order: nothing was charged.
    await expect(page.getByRole("button", { name: "Pago", exact: true })).toBeVisible();

    // Clear the line before the next test.
    const backspace = page.locator(".numpad button", { hasText: /^⌫$/ });
    await backspace.click();
    await backspace.click();
    await expect(lines()).toHaveCount(0);
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

test("devolución: no pide PIN y el par vuelve al stock", async () => {
    const before = await stock(fx.in_stock);
    await page.locator(".pos-topheader").getByText("Órdenes").click();
    await page.getByText("Activo", { exact: true }).first().click();
    await page.locator(".dropdown-item", { hasText: /^Pagado$/ }).click();
    // The oldest paid order is the first test's sale of in_stock.
    await page.locator(".order-row").last().click();
    await expect(page.locator(".orderline").first()).toContainText("37");
    await page.locator(".orderline").first().click();
    await page.locator(".numpad button", { hasText: /^1$/ }).click();
    await page.getByRole("button", { name: "Reembolso" }).click();

    // Odoo goes straight to the payment screen with -48,00; no stock question.
    await expect(page.locator(".paymentmethod").first()).toBeVisible();
    await expect(page.locator(".payment-screen, .o_payment_screen, body").first()).toContainText("-48,00");
    await expect(page.locator(".modal-dialog")).toHaveCount(0);
    await payCash({ onPaymentScreen: true });
    await expect.poll(() => stock(fx.in_stock)).toBe(before + 1);
    await expect(page.locator(".modal-dialog")).toHaveCount(0);
});

test("dos cajas a la vez: el último par se cobra una sola vez", async ({ browser }) => {
    const other = await browser.newContext();
    const till2 = await other.newPage();
    try {
        await login(till2);
        await till2.goto(`${fx.url}/pos/ui?config_id=${fx.config_id}`);
        await till2.getByText(/Abrir caja|Desbloquear/).first().click();
        await till2.locator("input.form-control").first().fill("1111");
        await till2.keyboard.press("Enter");
        // Odoo may ask the second screen for the opening cash again, although
        // the session is already open; confirming it changes nothing.
        const opening = till2.getByRole("button", { name: "Abrir caja registradora" });
        await till2.waitForTimeout(1500);
        if (await opening.isVisible()) {
            await opening.click();
        }
        await expect(till2.locator(".modal-dialog")).toHaveCount(0);

        // Both tills put the last pair in an order: each still sees 1 in stock.
        await scan(fx.last_pair);
        await expect(lines()).toHaveCount(1);
        await scan(fx.last_pair, till2);
        await expect(lines(till2)).toHaveCount(1);
        await expect(till2.locator(".modal-dialog")).toHaveCount(0);

        // The first till charges it.
        await payCash();
        await expect.poll(() => stock(fx.last_pair)).toBe(0);

        // The second till is stopped at Payment: the server says it is gone.
        await till2.getByRole("button", { name: "Pago", exact: true }).click();
        await expect(dialog(till2)).toContainText("No hay stock suficiente");
        await dialog(till2).getByRole("button", { name: "Cancelar" }).click();
        await expect(till2.getByRole("button", { name: "Pago", exact: true })).toBeVisible();
        expect(await stock(fx.last_pair)).toBe(0);
    } finally {
        await other.close();
    }
});

test("cierre de caja: cuadra con las ventas del día, también las de sin conexión", async () => {
    const menu = page.locator(".pos-topheader button:has(.fa-bars)").first();
    // A salesperson cannot close the till; the manager does (as in the manual).
    await menu.click();
    await expect(page.getByText("Volver a cargar datos")).toBeVisible();
    await expect(page.getByText("Cerrar caja registradora")).toHaveCount(0);
    await page.keyboard.press("Escape");
    await page.locator(".pos-topheader .fa-unlock").first().click();
    await page.getByText(/Desbloquear/).first().click();
    await page.locator("input.form-control").first().fill("4321");
    await page.keyboard.press("Enter");

    await menu.click();
    await page.getByText("Cerrar caja registradora").first().click();
    const closing = dialog();
    // Sale + authorized sale + offline sale - refund + last pair = 3 × 48.
    await expect(closing).toContainText("5 órdenes: $ 144,00");
    await closing.locator("input").first().fill("144");
    await expect(closing).toContainText("Diferencia");
    await closing.getByRole("button", { name: "Cerrar caja registradora" }).click();

    // (After closing, Odoo reloads the till and starts the next session.)
    const first = async () => (await rpc("pos.session", "search_read", [[]], {
        fields: ["state", "cash_register_difference", "move_id", "order_count"], order: "id", limit: 1,
    }))[0];
    await expect.poll(async () => (await first()).state, { timeout: 60_000 }).toBe("closed");
    const session = await first();
    expect(session.order_count).toBe(5);
    expect(session.cash_register_difference).toBeCloseTo(0, 2);
    expect(session.move_id).toBeTruthy();
    // Stock after closing is what the sales left.
    expect([
        await stock(fx.in_stock), await stock(fx.one_left),
        await stock(fx.no_stock), await stock(fx.last_pair),
    ]).toEqual([5, 0, -1, 0]);
});

test("día siguiente: un par con stock negativo pide PIN otra vez y se vende", async () => {
    // After closing, Odoo reloads the till; open the next session.
    await page.goto(`${fx.url}/pos/ui?config_id=${fx.config_id}`);
    await page.getByText(/Abrir caja|Desbloquear/).first().click();
    await page.locator("input.form-control").first().fill("1111");
    await page.keyboard.press("Enter");
    const opening = page.getByRole("button", { name: "Abrir caja registradora" });
    await expect(opening).toBeVisible();
    await opening.click();
    expect(await stock(fx.no_stock)).toBe(-1); // oversold yesterday

    await scan(fx.no_stock);
    await expect(dialog()).toContainText("No hay stock suficiente");
    await dialog().getByRole("button", { name: "Ingresar PIN del encargado" }).click();
    await typePinOnPad("4321");
    await expect(lines()).toHaveCount(1);
    await payCash();
    await expect.poll(() => stock(fx.no_stock)).toBe(-2);

    // And again at -2: still the stock question, never an error.
    await scan(fx.no_stock);
    await expect(dialog()).toContainText("No hay stock suficiente");
    await dialog().getByRole("button", { name: "Cancelar" }).click();
    await expect(lines()).toHaveCount(0);
    await expect(page.getByText("Ocurrió un error")).toHaveCount(0);
});
