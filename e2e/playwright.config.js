// @ts-check
const { defineConfig } = require("@playwright/test");

module.exports = defineConfig({
    testDir: ".",
    testMatch: "*.spec.js",
    timeout: 120_000,
    expect: { timeout: 15_000 },
    workers: 1,
    reporter: [["list"]],
    use: {
        viewport: { width: 1280, height: 800 },
        locale: "es-EC",
        screenshot: "only-on-failure",
    },
    outputDir: "test-results",
});
