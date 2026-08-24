const { test, expect } = require('@playwright/test');

test('app loads and displays PUNK-SCOUT title and iframe content', async ({ page }) => {
  await page.goto('/');
  await expect(page).toHaveTitle(/PUNK-SCOUT/i);

  // Verify the Streamlit iframe component loads
  const iframe = page.frameLocator('iframe').first();
  await expect(iframe.locator('body')).toBeVisible({ timeout: 15000 });
});
